"""Independent diagnostic of official Auth and revocation across storage I/O.
No source mutations. OfficialSharedUploadTests supplies actual GoTrue users,
PostgreSQL session state, independent HTTP replicas, and private disk storage.
This is a diagnostic probe, not the repository's official test-suite result.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import psycopg
from engine.cloud_auth import AuthVerifier, PostgresSessionChecker, TrustedKey
from engine.cloud_http import HTTPConfig
from engine.object_store import LocalObjectStore
from engine.shared_upload_authority import PostgresUploadAuthority
from engine.shared_upload_http import SharedMediaHTTPApplication, shared_http_server
from upload_tests.g3a1.support import HTTPReplica
from shared_auth_tests.g3a1auth.test_official_shared import OfficialSharedUploadTests as Lab


def emit(event, **data):
    print(json.dumps({'event':event, **data},sort_keys=True), flush=True)


def token():
    return Lab.auth('/token?grant_type=password', {'email':Lab.emails[0], 'password':Lab.password})['access_token']


def reserve(bearer, suffix, payload=b'abc'):
    status, grant = Lab.a.call('POST', f'/v1/orgs/{Lab.orgs[0]}/uploads', {
        'request_key':suffix+'-'+uuid4().hex, 'size_bytes':len(payload), 'sha256':hashlib.sha256(payload).hexdigest()}, token=bearer)
    assert status == 201, (status, grant)
    return grant


def membership(present):
    with psycopg.connect(Lab.admin_dsn) as db:
        if present:
            db.execute("INSERT INTO public.memberships VALUES(%s,%s,'owner') ON CONFLICT DO NOTHING", (Lab.orgs[0],Lab.users[0]))
        else:
            db.execute('DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s', (Lab.orgs[0],Lab.users[0]))


@contextmanager
def paused_http(on='put', timeout=5):
    entered, release = threading.Event(), threading.Event()
    class PausedStore(LocalObjectStore):
        def put_file(self, *args, **kwargs):
            if on=='put':
                entered.set()
                assert release.wait(timeout), 'storage publication was never released'
            return super().put_file(*args, **kwargs)
        def get_file(self, *args, **kwargs):
            if on=='get':
                entered.set()
                assert release.wait(timeout), 'storage verification was never released'
            return super().get_file(*args, **kwargs)
    checker=PostgresSessionChecker(Lab.api_dsn)
    verifier=AuthVerifier(issuer=os.environ['G3A_AUTH_ISSUER'], keys={'':TrustedKey('HS256',os.environ['G3A_AUTH_JWT_SECRET'])}, session_checker=checker)
    authority=PostgresUploadAuthority(Lab.api_dsn,Lab.signing_key,lease_seconds=2)
    app=SharedMediaHTTPApplication(dsn=Lab.api_dsn,verifier=verifier,store=PausedStore(Lab.root/'objects'),authority=authority,config=HTTPConfig(request_timeout_seconds=1,max_connections=2))
    server=shared_http_server(app)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.02},daemon=True)
    thread.start()
    client=object.__new__(HTTPReplica)
    client.port,client._token_factory=server.server_port,None
    try:
        yield client, entered, release, checker, app
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def state(grant):
    with psycopg.connect(Lab.admin_dsn) as db:
        return db.execute('SELECT r.state, (SELECT count(*) FROM g3a1_private.upload_receipts x WHERE x.id=r.id), l.used_bytes,l.used_count FROM g3a1_private.upload_reservations r JOIN g3a1_private.upload_limits l USING(organization_id) WHERE r.id=%s',(grant['id'],)).fetchone()


def raw(port,data):
    with socket.create_connection(('127.0.0.1',port),timeout=3) as sock:
        sock.sendall(data)
        parts=[]
        while True:
            block=sock.recv(65536)
            if not block: break
            parts.append(block)
    wire=b''.join(parts)
    head,body=wire.split(b'\r\n\r\n',1)
    return {'status':int(head.split(b' ',2)[1]),'body':json.loads(body),'no_store':b'Cache-Control: no-store' in head,'close':b'Connection: close' in head}


def main():
    Lab.setUpClass()
    try:
        emit('provenance', source_head='e40357d08fa3c0b258b63c0d3a811cbab2b23206', component='official GoTrue 2.197.0/PostgreSQL 16.15', replica_pids=[Lab.a.pid,Lab.b.pid], diagnostic_pid=os.getpid())
        bearer=token()
        req={'request_key':'auth-boundary-'+uuid4().hex,'size_bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
        cross=[rep.call('POST',f'/v1/orgs/{Lab.orgs[1]}/uploads',req,token=bearer)[0] for rep in (Lab.a,Lab.b)]
        fabricated=[rep.call('POST',f'/v1/orgs/{Lab.orgs[0]}/uploads',req,headers={'X-User-ID':str(Lab.users[0]),'X-Organization-ID':str(Lab.orgs[0])})[0] for rep in (Lab.a,Lab.b)]
        assert cross==[403,403] and fabricated==[401,401]
        emit('real_auth_cross_org_and_fabricated_headers',cross_org_statuses=cross,fabricated_header_statuses=fabricated)
        downgraded=reserve(bearer,'viewer-grant')
        with psycopg.connect(Lab.admin_dsn) as db:
            db.execute("UPDATE public.memberships SET role='viewer' WHERE organization_id=%s AND user_id=%s",(Lab.orgs[0],Lab.users[0]))
        try:
            admitted=[rep.call('POST',f'/v1/orgs/{Lab.orgs[0]}/uploads',req,token=bearer)[0] for rep in (Lab.a,Lab.b)]
            uses=[rep.call('PUT',downgraded['upload_path'],b'abc',token=bearer)[0] for rep in (Lab.a,Lab.b)]
            assert admitted==[403,403] and uses==[403,403]
            emit('real_auth_role_downgrade',reserve_statuses=admitted,existing_grant_use_statuses=uses)
        finally:
            with psycopg.connect(Lab.admin_dsn) as db:
                db.execute("UPDATE public.memberships SET role='owner' WHERE organization_id=%s AND user_id=%s",(Lab.orgs[0],Lab.users[0]))
        expiring=token()
        checked=PostgresSessionChecker(Lab.api_dsn)
        verified=AuthVerifier(issuer=os.environ['G3A_AUTH_ISSUER'],keys={'':TrustedKey('HS256',os.environ['G3A_AUTH_JWT_SECRET'])},session_checker=checked).verify(expiring)
        with psycopg.connect(Lab.admin_dsn) as db:
            db.execute("UPDATE auth.sessions SET not_after=now()-interval '1 second' WHERE id=%s",(verified.session_id,))
        expired=[rep.call('POST',f'/v1/orgs/{Lab.orgs[0]}/uploads',req,token=expiring)[0] for rep in (Lab.a,Lab.b)]
        assert expired==[401,401]
        emit('real_auth_expired_retained_session',statuses=expired,session_checker_live=checked(verified.user_id,verified.session_id))
        grant=reserve(bearer,'logout-during-publish')
        with paused_http('put') as (client,entered,release,checker,app):
            identity=app.verifier.verify(bearer)
            with ThreadPoolExecutor(max_workers=1) as ex:
                request=ex.submit(client.call,'PUT',grant['upload_path'],b'abc',token=bearer)
                assert entered.wait(3)
                Lab.auth('/logout',token=bearer)
                live=checker(identity.user_id,identity.session_id)
                release.set()
                status,receipt=request.result(timeout=10)
            fresh=[rep.call('GET',f'/v1/orgs/{Lab.orgs[0]}/uploads/{grant["id"]}',token=bearer)[0] for rep in (Lab.a,Lab.b)]
            emit('logout_committed_during_storage_before_receipt',session_live_after_logout=live,response_status=status,response_state=receipt.get('state'),database_state=state(grant),fresh_request_statuses=fresh)
        bearer=token()
        peer=Lab.auth('/token?grant_type=password',{'email':Lab.emails[2],'password':Lab.password})['access_token']
        receipt_path=f'/v1/orgs/{Lab.orgs[0]}/uploads/{grant["id"]}'
        peer_get=[rep.call('GET',receipt_path,token=peer)[0] for rep in (Lab.a,Lab.b)]
        peer_put=[rep.call('PUT',grant['upload_path'],b'abc',token=peer)[0] for rep in (Lab.a,Lab.b)]
        assert peer_get==[400,400] and peer_put==[400,400]
        emit('same_org_other_real_owner_denied',receipt_statuses=peer_get,grant_use_statuses=peer_put)
        for method in ('GET','PUT'):
            with paused_http('get') as (client,entered,release,checker,app):
                path=f'/v1/orgs/{Lab.orgs[0]}/uploads/{grant["id"]}' if method=='GET' else grant['upload_path']
                with ThreadPoolExecutor(max_workers=1) as ex:
                    request=ex.submit(client.call,method,path,None if method=='GET' else b'abc',token=bearer)
                    assert entered.wait(3)
                    membership(False)
                    release.set()
                    try:
                        status,receipt=request.result(timeout=10)
                        fresh=[rep.call('GET',f'/v1/orgs/{Lab.orgs[0]}/uploads/{grant["id"]}',token=bearer)[0] for rep in (Lab.a,Lab.b)]
                        emit('membership_revoked_during_existing_receipt_verification',method=method,response_status=status,response_state=receipt.get('state'),fresh_request_statuses=fresh,database_state=state(grant))
                    finally:
                        membership(True)
        grant2=reserve(bearer,'membership-during-new-publish')
        with paused_http('put') as (client,entered,release,checker,app):
            with ThreadPoolExecutor(max_workers=1) as ex:
                request=ex.submit(client.call,'PUT',grant2['upload_path'],b'abc',token=bearer)
                assert entered.wait(3)
                membership(False)
                release.set()
                try:
                    status,response=request.result(timeout=10)
                    emit('membership_revoked_during_new_publication',response_status=status,response=response,database_state=state(grant2))
                finally:
                    membership(True)
        with paused_http('none') as (client,entered,release,checker,app):
            route=f'/v1/orgs/{Lab.orgs[0]}/uploads/{grant2["id"]}/resume'
            cases={
                'duplicate_content_length':f'POST {route} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n',
                'transfer_encoding':f'POST {route} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n',
                'body_cap':f'POST {route} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1048577\r\n\r\n',
                'duplicate_authorization':f'POST {route} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nAuthorization: Bearer {bearer}\r\nAuthorization: Bearer {bearer}\r\n\r\n',
            }
            for name,data in cases.items():emit('transport',case=name,**raw(client.port,data.encode()))
            for host in ('0.0.0.0','::1','10.0.0.1'):
                try:server=shared_http_server(app,host=host)
                except ValueError:emit('binding_rejected',host=host)
                else:
                    server.server_close()
                    raise AssertionError('unsafe binding admitted')
            with socket.create_connection(('127.0.0.1',client.port),timeout=3) as sock:
                started=time.monotonic()
                sock.sendall(f'POST {route} HTTP/1.0\r\nContent-Length: 10\r\n\r\na'.encode())
                time.sleep(0.4);sock.sendall(b'b');time.sleep(0.4);sock.sendall(b'c')
                wire=sock.recv(8192)
                emit('transport',case='absolute_receive_deadline',status=int(wire.split(b' ',2)[1]),elapsed_seconds=round(time.monotonic()-started,3))
        emit('completed',sqlite_files=len(list(Lab.root.rglob('*.sqlite*'))))
    finally:
        membership(True)
        Lab.tearDownClass()


if __name__=='__main__': main()
