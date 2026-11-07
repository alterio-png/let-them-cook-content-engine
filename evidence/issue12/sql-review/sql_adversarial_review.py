"""Independent Issue12 SQL review. Disposable loopback fixtures only; no source edits."""
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

OUT = Path('/workspace/issue12-evidence/sql-review')
ADMIN = 'postgresql://postgres:issue12-local-only@127.0.0.1:55611/g1_issue12_sql'
API = 'postgresql://i12_api:local-synthetic-api@127.0.0.1:55611/g1_issue12_sql'
SERVICE = 'postgresql://i12_service:local-synthetic-service@127.0.0.1:55611/g1_issue12_sql'
ORG = '12121212-1212-4212-8212-121212121212'
OTHER_ORG = '12121212-1212-4212-8212-121212121213'
USER = '12121212-1212-4212-8212-121212121214'
PEER = '12121212-1212-4212-8212-121212121215'
VIEWER = '12121212-1212-4212-8212-121212121216'
BODY = b'issue12 synthetic local fixture'
SHA = hashlib.sha256(BODY).hexdigest()
checks = []

def record(name, **details):
    item = {'check': name, 'status': 'PASS', **details}
    checks.append(item)
    print(json.dumps(item, default=str), flush=True)

@contextmanager
def actor(user=USER, service=False):
    with psycopg.connect(SERVICE if service else API, row_factory=dict_row) as db:
        db.execute('SET LOCAL statement_timeout=\'8s\'')
        db.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier('g3a1_upload_reconciler' if service else 'authenticated')))
        if user is not None:
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (user,))
        yield db

def query(statement, params=(), user=USER, service=False):
    with actor(user, service) as db:
        return db.execute(statement, params).fetchone()['v']

def rpc(name, *params, user=USER, service=False):
    statement = sql.SQL('SELECT g3a1_private.{}({}) AS v').format(sql.Identifier(name), sql.SQL(',').join(sql.Placeholder() for _ in params))
    return query(statement, params, user, service)

def expect_error(name, state, fn):
    try:
        fn()
    except psycopg.Error as error:
        assert error.sqlstate == state, (name, state, error.sqlstate)
        record(name, expected_sqlstate=state, observed_sqlstate=error.sqlstate)
    else:
        raise AssertionError(name + ': call unexpectedly succeeded')

def reserve(key, ttl=120):
    return rpc('reserve_upload', ORG, key, len(BODY), SHA, ttl)

def claim(grant, lease=20):
    return rpc('claim_upload', ORG, grant['id'], lease)

def complete(grant, deadline=None, service=False, token=None, fence=None):
    params = (ORG, grant['id'], token or grant['lease_token'], fence if fence is not None else grant['fence'], grant['object_key'], len(BODY), SHA, 'synthetic-generation-1')
    return rpc('internal_complete_upload' if service else 'complete_upload_before', *params, *((deadline,) if not service else ()), service=service)

def snapshot():
    with psycopg.connect(ADMIN, row_factory=dict_row) as db:
        return db.execute("""SELECT q.used_bytes,q.used_count,
          (SELECT count(*) FROM g3a1_private.upload_receipts r WHERE r.organization_id=q.organization_id) receipts,
          (SELECT count(*) FROM g3a1_private.upload_events e WHERE e.organization_id=q.organization_id) events,
          (SELECT coalesce(sum(size_bytes),0) FROM g3a1_private.upload_reservations r WHERE r.organization_id=q.organization_id AND state<>'expired') retained_bytes,
          (SELECT count(*) FROM g3a1_private.upload_reservations r WHERE r.organization_id=q.organization_id AND state<>'expired') retained_count
          FROM g3a1_private.upload_limits q WHERE organization_id=%s""", (ORG,)).fetchone()

def future_deadline(seconds=20):
    with psycopg.connect(ADMIN) as db:
        return db.execute("SELECT clock_timestamp()+make_interval(secs=>%s)", (seconds,)).fetchone()[0]

def main():
    with psycopg.connect(ADMIN) as db:
        for role, password, parent in (('i12_api', 'local-synthetic-api', 'authenticated'), ('i12_service', 'local-synthetic-service', 'g3a1_upload_reconciler')):
            db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(role), sql.Literal(password)))
            db.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(parent), sql.Identifier(role)))
        db.execute('INSERT INTO auth.users(id) VALUES(%s),(%s),(%s)', (USER, PEER, VIEWER))
        db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'Issue12 synthetic SQL review'),(%s,'Issue12 other synthetic org')", (ORG, OTHER_ORG))
        db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor'),(%s,%s,'editor'),(%s,%s,'viewer')", (ORG, USER, ORG, PEER, ORG, VIEWER))
        db.execute('INSERT INTO g3a1_private.upload_limits(organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds) VALUES(%s,100000,1000,1000,120,20),(%s,1000,10,1000,120,20)', (ORG, OTHER_ORG))
        catalog = {}
        catalog['version'] = db.execute('SELECT version()').fetchone()[0]
        catalog['functions'] = db.execute("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,p.proacl::text
            FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='g3a1_private' ORDER BY p.proname""").fetchall()
        catalog['tables'] = db.execute("""SELECT relname,relrowsecurity,pg_get_userbyid(relowner),relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='g3a1_private' AND c.relkind='r' ORDER BY relname""").fetchall()
        catalog['policies'] = db.execute("SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='g3a1_private' ORDER BY tablename,policyname").fetchall()
        assert len(catalog['functions']) == 27, len(catalog['functions'])
        assert len(catalog['tables']) == 4
        assert all(row[1] for row in catalog['tables'])
        assert all('search_path=""' in row[4] for row in catalog['functions'])
        assert db.execute("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
           LATERAL aclexplode(p.proacl) a WHERE n.nspname='g3a1_private' AND a.grantee=0 AND a.privilege_type='EXECUTE'""").fetchone()[0] == 0
    (OUT/'catalog.json').write_text(json.dumps(catalog, indent=2, default=str)+'\n')
    record('catalog: all 27 functions have empty search_path and no PUBLIC EXECUTE; four tables enforce RLS')

    g = reserve('immutable-request')
    assert reserve('immutable-request', 1)['id'] == g['id']
    assert reserve('immutable-request', 1)['expires_at'] == g['expires_at']
    assert snapshot()['used_count'] == 1
    record('matching retry retains grant/expiry and single quota charge')
    expect_error('same org request key conflicting owner', '23505', lambda: rpc('reserve_upload', ORG, 'immutable-request', len(BODY), SHA, 120, user=PEER))
    expect_error('same org request key conflicting SHA', '23505', lambda: rpc('reserve_upload', ORG, 'immutable-request', len(BODY), 'a'*64, 120))
    expect_error('same org request key conflicting size', '23505', lambda: rpc('reserve_upload', ORG, 'immutable-request', len(BODY)+1, SHA, 120))
    expect_error('viewer cannot reserve', '42501', lambda: rpc('reserve_upload', ORG, 'viewer-denied', len(BODY), SHA, 120, user=VIEWER))
    expect_error('null subject cannot claim', '42501', lambda: rpc('claim_upload', ORG, g['id'], 20, user=None))
    expect_error('cross organization claim denied', '42501', lambda: rpc('claim_upload', OTHER_ORG, g['id'], 20))
    expect_error('authenticated raw mutation denied', '42501', lambda: query('UPDATE g3a1_private.upload_limits SET used_count=0 RETURNING used_count AS v'))
    expect_error('authenticated internal claim denied', '42501', lambda: rpc('internal_claim_reconciliation', ORG, g['id'], 20))
    expect_error('authenticated private helper denied', '42501', lambda: rpc('_reservation_json', g['id']))
    expect_error('service raw SELECT denied', '42501', lambda: query('SELECT count(*) AS v FROM g3a1_private.upload_reservations', service=True))
    expect_error('service user-facing reserve denied', '42501', lambda: rpc('reserve_upload', ORG, 'service-reserve-denied', len(BODY), SHA, 120, service=True))

    # A caller-controlled temp schema is first in search_path; helper SQL remains closed.
    with actor() as db:
        db.execute("CREATE FUNCTION pg_temp.gen_random_uuid() RETURNS uuid LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'temp UUID shadow executed'; END $$")
        db.execute("CREATE FUNCTION pg_temp.clock_timestamp() RETURNS timestamptz LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'temp clock shadow executed'; END $$")
        db.execute('SET LOCAL search_path=pg_temp,public')
        spoof = db.execute('SELECT g3a1_private.reserve_upload(%s,%s,%s,%s,120) AS v', (ORG, 'temp-shadow', len(BODY), SHA)).fetchone()['v']
        assert spoof['id'] != g['id']
    record('temp schema/function shadows cannot hijack definer builtins')
    assert query('SELECT count(*) AS v FROM g3a1_private.upload_reservations WHERE id=%s', (g['id'],), user=PEER) == 0
    record('RLS hides same-org other-owner reservation')

    current = claim(g)
    rpc('mark_upload_uncertain', ORG, g['id'], current['lease_token'], current['fence'], 'SYNTHETIC_FAILURE')
    assert snapshot()['used_count'] == 2
    next_claim = rpc('reconcile_own_upload', ORG, g['id'], 20)
    assert next_claim['fence'] == current['fence']+1
    before = snapshot()
    expect_error('stale fence cannot complete', '40001', lambda: complete(current, future_deadline()))
    assert snapshot() == before
    receipt = complete(next_claim, future_deadline())
    assert receipt['owner_user_id'] == USER
    assert complete(next_claim, future_deadline()) == receipt
    assert snapshot()['used_count'] == 2
    record('fresh fenced completion/replay has single immutable receipt; uploaded/uncertain retain charge')

    # Real row lock blocks completion past its absolute database deadline.
    overdue = claim(reserve('post-lock-deadline'))
    blocker = psycopg.connect(ADMIN)
    blocker.execute('SELECT 1 FROM g3a1_private.upload_limits WHERE organization_id=%s FOR UPDATE', (ORG,))
    deadline = future_deadline(0.3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(complete, overdue, deadline)
        time.sleep(0.65)
        assert not job.done()
        blocker.commit()
        blocker.close()
        expect_error('deadline rechecked after actual quota row lock wait', '40001', job.result)
    assert rpc('get_upload', ORG, overdue['id'])['state'] == 'publishing'
    record('overdue completion leaves no receipt after lock wait')

    # A successful initial check must still roll back if audit insertion stalls.
    trigger_grant = claim(reserve('post-write-deadline'))
    with psycopg.connect(ADMIN) as db:
        db.execute("CREATE FUNCTION g3a1_private.i12_delay_uploaded_audit() RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $$ BEGIN IF NEW.event_type='uploaded' THEN PERFORM pg_sleep(0.6); END IF; RETURN NEW; END $$")
        db.execute('CREATE TRIGGER i12_delayed_audit BEFORE INSERT ON g3a1_private.upload_events FOR EACH ROW EXECUTE FUNCTION g3a1_private.i12_delay_uploaded_audit()')
    before = snapshot()
    try:
        expect_error('deadline rechecked after delayed audit write', '40001', lambda: complete(trigger_grant, future_deadline(0.3)))
    finally:
        with psycopg.connect(ADMIN) as db:
            db.execute('DROP TRIGGER i12_delayed_audit ON g3a1_private.upload_events')
            db.execute('DROP FUNCTION g3a1_private.i12_delay_uploaded_audit()')
    assert snapshot() == before
    assert rpc('get_upload', ORG, trigger_grant['id'])['state'] == 'publishing'
    record('post-write deadline failure atomically rolls back receipt/state/audit')

    expired = reserve('never-claimed-expiry', 1)
    before = snapshot()
    with psycopg.connect(ADMIN) as db:
        db.execute("CREATE FUNCTION g3a1_private.i12_reject_expiry_audit() RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $$ BEGIN IF NEW.event_type='expired' THEN RAISE EXCEPTION 'synthetic expiry audit failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        db.execute('CREATE TRIGGER i12_reject_expiry BEFORE INSERT ON g3a1_private.upload_events FOR EACH ROW EXECUTE FUNCTION g3a1_private.i12_reject_expiry_audit()')
    time.sleep(1.1)
    try:
        expect_error('expiry audit failure rejects whole refund transaction', '23514', lambda: rpc('expire_unused_uploads', ORG, 1000))
        assert snapshot() == before
        assert rpc('get_upload', ORG, expired['id'])['state'] == 'reserved'
    finally:
        with psycopg.connect(ADMIN) as db:
            db.execute('DROP TRIGGER i12_reject_expiry ON g3a1_private.upload_events')
            db.execute('DROP FUNCTION g3a1_private.i12_reject_expiry_audit()')
    assert rpc('expire_unused_uploads', ORG, 1000) == 1
    assert rpc('expire_unused_uploads', ORG, 1000) == 0
    assert snapshot()['used_count'] == before['used_count']-1
    assert snapshot()['used_bytes'] == before['used_bytes']-len(BODY)
    assert reserve('never-claimed-expiry', 120)['id'] == expired['id']
    assert reserve('never-claimed-expiry', 120)['state'] == 'expired'
    record('unused expiry refunds exactly once; expired fingerprint remains tombstone')

    # Hold an authorized transaction and prove role downgrade waits for it.
    member_tx = psycopg.connect(API)
    member_tx.execute('SET LOCAL ROLE authenticated')
    member_tx.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (USER,))
    member_tx.execute('SELECT g3a1_private.reserve_upload(%s,%s,%s,%s,120)', (ORG, 'membership-lock', len(BODY), SHA))
    def downgrade():
        with psycopg.connect(ADMIN) as db:
            db.execute("UPDATE public.memberships SET role='viewer' WHERE organization_id=%s AND user_id=%s", (ORG, USER))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(downgrade)
        time.sleep(0.3)
        assert not job.done()
        member_tx.commit()
        member_tx.close()
        job.result()
    expect_error('current downgraded owner cannot mutate held claim', '42501', lambda: rpc('heartbeat_upload', ORG, overdue['id'], overdue['lease_token'], overdue['fence'], 20))
    record('membership FOR SHARE delays downgrade until authorized transaction commits')

    # Historical identity survives actual auth-user deletion; recovery remains service-only.
    with psycopg.connect(ADMIN) as db:
        db.execute('DELETE FROM auth.users WHERE id=%s', (USER,))
    expect_error('deleted auth owner loses get access', '42501', lambda: rpc('get_upload', ORG, g['id']))
    service_read = rpc('internal_claim_reconciliation', ORG, g['id'], 20, service=True)
    assert service_read['state'] == 'uploaded'
    assert service_read['receipt'] == receipt
    final = snapshot()
    assert final['used_bytes'] == final['retained_bytes']
    assert final['used_count'] == final['retained_count']
    record('deleted-owner historical receipt retained and accessible to isolated recovery role; exact cumulative counters', final_counters=final)
    (OUT/'results.json').write_text(json.dumps({'checks':checks,'count':len(checks),'final_counters':final}, indent=2, default=str)+'\n')
    print('ALL INDEPENDENT ADVERSARIAL SQL CHECKS PASSED', flush=True)

if __name__ == '__main__':
    main()
