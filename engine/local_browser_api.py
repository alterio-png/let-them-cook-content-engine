"""R0 loopback browser/API adapter. Never runs media parsers in HTTP handlers."""
from contextlib import contextmanager
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
from socketserver import ThreadingMixIn
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg

from .cloud_auth import AuthError, AuthVerifier, PostgresSessionChecker, TrustedKey
from .cloud_http import HTTPConfig, HTTPError, _BoundedHTTPServer, _json, _uuid
from .local_observability import ConnectionBudget, Metrics
from .object_store import LocalObjectStore, ObjectStoreError
from .shared_upload_authority import PostgresUploadAuthority
from .shared_upload_http import SharedMediaHTTPApplication, _SharedHandler


UI_ROOT = Path(__file__).resolve().parents[1] / 'local_ui'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src blob:; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


def _loopback_url(value):
    p = urllib.parse.urlsplit(value)
    if (p.scheme != 'http' or p.hostname != '127.0.0.1' or not p.port
            or p.username or p.password or p.query or p.fragment or p.path not in ('', '/')):
        raise ValueError('R0 Auth must be an explicit loopback HTTP endpoint')
    return value.rstrip('/')


class BudgetedAuthority(PostgresUploadAuthority):
    def __init__(self, *args, budget, **kwargs):
        self.budget = budget
        super().__init__(*args, **kwargs)

    @contextmanager
    def _connection(self, user_id):
        with self.budget.hold('upload'):
            with super()._connection(user_id) as db:
                yield db


class BudgetedSessions:
    def __init__(self, dsn, budget, metrics):
        self.checker, self.budget, self.metrics = PostgresSessionChecker(dsn), budget, metrics
        self.slots = threading.BoundedSemaphore(1)
        self.context = threading.local()

    def begin_request(self):
        self.context.unavailable = False

    @property
    def unavailable(self):
        return getattr(self.context, 'unavailable', False)

    def __call__(self, user, session):
        started = time.monotonic()
        if not self.slots.acquire(timeout=0.1):
            self.context.unavailable = True
            self.metrics('authentication', 'backpressure', error_code='session_budget')
            raise AuthError('session backend busy')
        try:
            with self.budget.hold('session'):
                valid = self.checker(user, session)
            self.metrics('authentication', 'accepted' if valid else 'denied',
                         elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            return valid
        except Exception:
            self.context.unavailable = True
            self.metrics('authentication', 'unavailable', error_code='session_backend')
            raise
        finally:
            self.slots.release()


class LocalBrowserApplication(SharedMediaHTTPApplication):
    def __init__(self, *, dsn, verifier, store, authority, auth_url, fixtures,
                 metrics=None, budget=None, config=None):
        self.metrics = metrics or Metrics()
        self.budget = budget or ConnectionBudget(6, self.metrics)
        self.auth_url = _loopback_url(auth_url)
        self.fixtures = {str(f['id']): dict(f) for f in fixtures}
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', x) for x in self.fixtures):
            raise ValueError('invalid trusted fixture identifier')
        super().__init__(dsn=dsn, verifier=verifier, store=store, authority=authority,
                         config=config or HTTPConfig(max_connections=4, max_db_connections=2))

    @contextmanager
    def _connection(self, identity):
        with self.budget.hold('api'):
            with super()._connection(identity) as db:
                yield db

    def _identity(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
            raise HTTPError(401, 'authentication_required')
        return self.verifier.verify(authorization[7:])

    def _auth(self, route, payload=None, token=None):
        started = time.monotonic()
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        request = urllib.request.Request(self.auth_url + route, method='POST', headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else b'')
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=3) as response:
                raw = response.read(32769)
                if len(raw) > 32768:
                    raise HTTPError(503, 'auth_response_limit')
                self.metrics('authentication', 'provider_response', status=response.status,
                             elapsed_ms=round((time.monotonic() - started) * 1000, 3))
                return json.loads(raw) if raw else {'logged_out': True}
        except urllib.error.HTTPError as error:
            denied = error.code in (400, 401, 403, 422)
            self.metrics('authentication', 'provider_denied' if denied else 'provider_backpressure' if error.code == 429 else 'provider_unavailable',
                         status=error.code, elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            raise HTTPError(401 if denied else 503,
                            'authentication_failed' if denied else 'auth_backpressure' if error.code == 429 else 'auth_unavailable') from None
        except (OSError, ValueError):
            self.metrics('authentication', 'provider_unavailable', error_code='transport_or_response',
                         elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            raise HTTPError(503, 'auth_unavailable') from None

    def _fixture(self, identity, identifier, kind):
        item = self.fixtures.get(identifier)
        if not item or kind not in {'video', 'caption'}:
            raise HTTPError(404, 'fixture_not_found')
        with self._connection(identity) as db:
            self._membership(db, identity, item['organization_id'])
        fd = os.open(item[kind + '_path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise HTTPError(409, 'fixture_integrity_failure')
            with os.fdopen(fd, 'rb', closefd=False) as handle:
                body = handle.read(self.config.max_body_bytes + 1)
        finally:
            os.close(fd)
        if (len(body) != item[kind + '_bytes'] or len(body) > self.config.max_body_bytes
                or hashlib.sha256(body).hexdigest() != item[kind + '_sha256']):
            raise HTTPError(409, 'fixture_integrity_failure')
        return 200, body, 'video/mp4' if kind == 'video' else 'application/x-subrip'

    def _local(self, method, path, authorization, body):
        if len(path) > 8192 or any(c in path for c in ('?', '#', '%')):
            raise HTTPError(400, 'invalid_path')
        if path in ASSETS and method == 'GET':
            file, mime = ASSETS[path]
            return 200, (UI_ROOT / file).read_bytes(), mime
        if path == '/health' and method == 'GET':
            return 200, {'status': 'local_api_ready', 'scope': 'LOCAL_ONLY'}, 'application/json'
        if len(body) > self.config.max_json_bytes:
            raise HTTPError(413, 'body_too_large')
        if path == '/local/v1/auth/login' and method == 'POST':
            data = _json(body, ('email', 'password'))
            if any(not isinstance(v, str) or not 1 <= len(v) <= 512 for v in data.values()):
                raise HTTPError(400, 'invalid_request')
            result = self._auth('/token?grant_type=password', data)
            return 200, {k: result[k] for k in ('access_token', 'expires_in', 'token_type') if k in result}, 'application/json'
        identity = self._identity(authorization)
        if path == '/local/v1/auth/logout' and method == 'POST':
            if body not in (b'', b'{}'):
                raise HTTPError(400, 'invalid_request')
            return 200, self._auth('/logout', token=authorization), 'application/json'
        if path == '/local/v1/me' and method == 'GET':
            with self._connection(identity) as db:
                orgs = db.execute('SELECT o.id,o.name,m.role FROM public.memberships m JOIN public.organizations o ON o.id=m.organization_id WHERE m.user_id=%s ORDER BY o.id', (identity.user_id,)).fetchall()
            return 200, {'user_id': str(identity.user_id), 'organizations': orgs}, 'application/json'
        if path == '/local/v1/fixtures' and method == 'GET':
            with self._connection(identity) as db:
                ids = {str(x['organization_id']) for x in db.execute('SELECT organization_id FROM public.memberships WHERE user_id=%s', (identity.user_id,))}
            return 200, {'fixtures': [{'id': f['id'], 'name': 'Amostra sintética local',
                'organization_id': f['organization_id'], 'video_name': 'synthetic.mp4',
                'caption_name': 'synthetic.srt', 'duration_ms': f['duration_ms']}
                for f in self.fixtures.values() if str(f['organization_id']) in ids]}, 'application/json'
        sample = re.fullmatch(r'/local/v1/fixtures/([A-Za-z0-9_-]+)/([a-z]+)', path)
        if sample and method == 'GET':
            return self._fixture(identity, *sample.groups())
        if path == '/local/v1/metrics' and method == 'GET':
            return 200, self.metrics.snapshot(), 'application/json'
        ingest = re.fullmatch(r'/local/v1/orgs/([^/]+)/ingestions(?:/([^/]+))?', path)
        if ingest:
            org, request_id = ingest.groups()
            org = _uuid(org)
            with self._connection(identity) as db:
                self._membership(db, identity, org, write=method == 'POST')
                if method == 'POST' and request_id is None:
                    data = _json(body, ('request_key', 'video_receipt_id', 'caption_receipt_id'))
                    if not isinstance(data['request_key'], str) or not 1 <= len(data['request_key']) <= 128:
                        raise HTTPError(400, 'invalid_request')
                    result = db.execute('SELECT g3br0_private.request_ingest(%s,%s,%s,%s) AS value',
                        (org, data['request_key'], _uuid(data['video_receipt_id']), _uuid(data['caption_receipt_id']))).fetchone()['value']
                    self.metrics('ingest', 'requested', physical=1)
                    return 202, result, 'application/json'
                if method == 'GET' and request_id:
                    result = db.execute('SELECT g3br0_private.get_ingest(%s,%s) AS value', (org, _uuid(request_id))).fetchone()['value']
                    if not result:
                        raise HTTPError(404, 'ingestion_not_found')
                    return 200, result, 'application/json'
            raise HTTPError(405, 'method_not_allowed')
        manifest = re.fullmatch(r'/local/v1/orgs/([^/]+)/renders/([^/]+)/manifest', path)
        if manifest and method == 'GET':
            org, job_id = map(_uuid, manifest.groups())
            with self._connection(identity) as db:
                self._membership(db, identity, org)
                job = self._job(db, org, job_id)
                if job['state'] != 'completed':
                    raise HTTPError(409, 'render_not_completed')
                result = job['output_manifest']
                with tempfile.TemporaryDirectory(prefix='r0-private-manifest-') as name:
                    stored_manifest = Path(name) / 'manifest.json'
                    self.store.get_file(org, result['generation'] + '/manifest.json',
                                        stored_manifest, max_bytes=65536)
                    if json.loads(stored_manifest.read_text(encoding='utf-8')) != result:
                        raise HTTPError(409, 'manifest_integrity_failure')
                    for i, output in enumerate(result['outputs']):
                        if not 0 < output['size_bytes'] <= self.config.max_download_bytes:
                            raise HTTPError(413, 'download_limit_exceeded')
                        target = Path(name) / f'{i}.mp4'
                        actual = self.store.get_file(org, output['key'], target, max_bytes=output['size_bytes'])
                        if (actual['sha256'], actual['size_bytes'], hashlib.sha256(target.read_bytes()).hexdigest()) != (output['sha256'], output['size_bytes'], output['sha256']):
                            raise HTTPError(409, 'output_integrity_failure')
            return 200, result, 'application/json'
        raise HTTPError(404, 'route_not_found')

    def handle(self, method, path, authorization, body=b''):
        started = time.monotonic()
        checker = self.verifier.session_checker
        if isinstance(checker, BudgetedSessions):
            checker.begin_request()
        stage = 'authentication' if path.startswith('/local/v1/auth/') else 'upload' if path.startswith('/v1/uploads/') else 'download' if path.startswith('/v1/downloads/') else 'ledger' if '/uploads' in path else 'api'
        try:
            if path in ASSETS or path == '/health' or path.startswith('/local/'):
                result = self._local(method, path, authorization, body)
            else:
                result = super().handle(method, path, authorization, body)
        except AuthError:
            result = 401, {'error': 'invalid_or_revoked_session'}, 'application/json'
        except HTTPError as error:
            result = error.status, {'error': error.code}, 'application/json'
        except ObjectStoreError:
            result = 409, {'error': 'private_storage_integrity_failure'}, 'application/json'
        except psycopg.Error as error:
            status = 403 if error.sqlstate == '42501' else 409 if error.sqlstate in {'23505', '23514', '22023', 'P0001', 'P0002'} else 503
            result = status, {'error': 'organization_access_denied' if status == 403 else 'request_conflict' if status == 409 else 'database_unavailable'}, 'application/json'
        status, response, mime = result
        if status == 401 and isinstance(checker, BudgetedSessions) and checker.unavailable:
            result = status, response, mime = 503, {'error': 'authentication_unavailable'}, 'application/json'
        self.metrics(stage, 'response', physical=1, status=status,
                     error_code=response.get('error', 'none') if isinstance(response, dict) else 'none',
                     elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        return result


class _BrowserHandler(_SharedHandler):
    def end_headers(self):
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        super().end_headers()

    def _request(self):
        expected = f'127.0.0.1:{self.server.server_port}'
        origins = self.headers.get_all('Origin', [])
        if self.headers.get_all('Host', []) != [expected] or origins and origins != ['http://' + expected]:
            self._receive_deadline.cancel()
            payload = b'{"error":"origin_or_host_denied"}'
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
            return
        return super()._request()

    do_GET = do_POST = do_PUT = do_DELETE = _request


class _BrowserServer(_BoundedHTTPServer):
    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.application.metrics('api', 'backpressure', status=503, physical=1, error_code='http_capacity')
            try:
                request.settimeout(self.application.config.request_timeout_seconds)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            ThreadingMixIn.process_request(self, request, client_address)
        except BaseException:
            self._slots.release()
            raise


def browser_http_server(application, *, host='127.0.0.1', port=0):
    if host != '127.0.0.1':
        raise ValueError('R0 browser is IPv4 loopback only')
    server = _BrowserServer((host, port), application)
    server.RequestHandlerClass = _BrowserHandler
    return server


def application_from_config(config):
    from .local_runtime import local_dsn
    metrics = Metrics(config.get('metrics_path'))
    budget = ConnectionBudget(6, metrics)
    fixtures = config['fixture_manifest']
    if isinstance(fixtures, str):
        fixtures = json.loads(Path(fixtures).read_text())
    key = base64.b64decode(config['signing_key'], validate=True)
    dsn = local_dsn(config['api_dsn'])
    verifier = AuthVerifier(issuer=config['issuer'], keys={'': TrustedKey('HS256', config['jwt_secret'])},
                            session_checker=BudgetedSessions(dsn, budget, metrics))
    authority = BudgetedAuthority(dsn, key, budget=budget, max_db_connections=2)
    return LocalBrowserApplication(dsn=dsn, verifier=verifier, store=LocalObjectStore(config['store_root']),
        authority=authority, auth_url=config['auth_url'], fixtures=fixtures['fixtures'], metrics=metrics, budget=budget)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--ready-file', type=Path)
    args = parser.parse_args()
    from .local_runtime import private_json
    config = private_json(args.config)
    app = application_from_config(config)
    server = browser_http_server(app, host=config.get('host', '127.0.0.1'), port=args.port if args.port is not None else config.get('port', 0))
    ready = {'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()}
    if args.ready_file:
        args.ready_file.write_text(json.dumps(ready) + '\n')
        args.ready_file.chmod(0o600)
    print(json.dumps(ready), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
