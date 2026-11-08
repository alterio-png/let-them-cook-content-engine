"""Independent R0 negative/concurrency/recovery probes against the real lab.

Run only through scripts/run_g3br0_local.sh --test. Missing PostgreSQL or the
actual official Auth/API/worker lab is an error, never a passing mocked skip.
Synthetic bytes only. Administrative DSNs stay in the private test driver.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from uuid import uuid4

import jwt
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class VerticalSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready = json.loads(Path(os.environ['G3BR0_READY_FILE']).read_text())
        cls.config = json.loads(Path(os.environ['G3BR0_TEST_CONFIG']).read_text())
        cls.base_url = cls.ready['url']
        from engine.local_runtime import local_dsn
        for key in ('admin_dsn', 'api_dsn', 'worker_dsn', 'ingest_dsn'):
            local_dsn(cls.config[key])  # Reject remote/production targets before I/O.
        for endpoint in (cls.base_url, cls.config['auth_url']):
            parsed = urllib.parse.urlsplit(endpoint)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password:
                raise ValueError('disposable loopback API/Auth required')
        if not cls.ready.get('api_pid') or not cls.ready.get('worker_pid'):
            raise ValueError('real supervised API and worker process identifiers required')
        cls.admin_dsn = cls.config['admin_dsn']
        cls.fixture = cls.config['fixtures'][0]
        cls.video = Path(cls.fixture['video_path']).read_bytes()
        cls.caption = Path(cls.fixture['caption_path']).read_bytes()
        cls.temp = tempfile.TemporaryDirectory(prefix='r0-independent-qa-')
        cls.temp_root = Path(cls.temp.name)
        cls.stats_lock = threading.Lock()
        cls.physical_requests = cls.backpressure_retries = 0
        cls.transport_retries = cls.retry_exhausted = 0
        cls.observed_statuses, cls.case_evidence, cls.workloads = {}, [], []
        cls.log = (cls.temp_root / 'replica.log').open('wb')
        config_path = Path(os.environ['G3BR0_TEST_CONFIG']).parent / 'api-config.json'
        ready_file = cls.temp_root / 'replica-ready.json'
        cls.replica = subprocess.Popen([sys.executable, '-m', 'engine.local_browser_api',
            '--config', str(config_path), '--port', '0', '--ready-file', str(ready_file)],
            stdout=cls.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while not ready_file.exists():
            if cls.replica.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('independent API replica failed to start')
            time.sleep(.05)
        replica = json.loads(ready_file.read_text())
        cls.second_url = replica['url']
        if cls.replica.pid == cls.ready.get('api_pid'):
            raise AssertionError('two actual independent OS processes required')
        with psycopg.connect(cls.admin_dsn) as db:
            if db.execute("SELECT to_regnamespace('g3br0_private')").fetchone()[0] is None:
                raise AssertionError('real G3B-R0 migration required')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'replica'):
            cls.replica.terminate()
            try:
                cls.replica.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.replica.kill()
                cls.replica.wait(timeout=5)
        if hasattr(cls, 'log'):
            cls.log.close()
        summary = {'suite': 'r0-independent-negative', 'physical_http_attempts': cls.physical_requests,
                   '503_retries': cls.backpressure_retries, 'transport_uncertain_retries': cls.transport_retries,
                   'retry_exhausted': cls.retry_exhausted, 'replicas': 2,
                   'statuses_observed': cls.observed_statuses, 'cases': cls.case_evidence,
                   'workloads': cls.workloads,
                   'postgres': 'real local PostgreSQL', 'auth': 'official local GoTrue',
                   'media': 'real local FFmpeg', 'cloud': 'NOT_VALIDATED'}
        print(json.dumps(summary, sort_keys=True))
        if cls.ready.get('output_dir'):
            evidence = Path(cls.ready['output_dir']) / 'independent-negative-summary.json'
            evidence.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
            evidence.chmod(0o600)
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    @classmethod
    def http(cls, method, path, body=None, *, token=None, url=None, headers=None,
             retry=True, lose_response=False):
        """Bounded retries: 503 or uncertain transport for keyed POST/safe GET.

        A full server may close immediately after sending 503, while the client
        is still writing its body. Read that response after BrokenPipe whenever
        possible. Unreadable transport is recorded separately, never as 503.
        """
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else b''
        target = urllib.parse.urlsplit(url or cls.base_url)
        send_headers = {'Content-Type': 'application/octet-stream' if isinstance(body, bytes) else 'application/json'}
        if token:
            send_headers['Authorization'] = 'Bearer ' + token
        send_headers.update(headers or {})
        deadline = time.monotonic() + 45
        transport_safe = method == 'GET' or (method == 'POST' and isinstance(body, dict) and 'request_key' in body)
        for attempt in range(16 if retry else 1):
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=10)
            try:
                with cls.stats_lock:
                    cls.physical_requests += 1
                try:
                    try:
                        conn.request(method, path, encoded, send_headers)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # A valid early HTTP rejection may already be readable.
                    response = conn.getresponse()
                    status = response.status
                    with cls.stats_lock:
                        cls.observed_statuses[str(status)] = cls.observed_statuses.get(str(status), 0) + 1
                    if lose_response:
                        # Real socket response abandonment after commit/headers.
                        return status, None
                    raw = response.read()
                    value = json.loads(raw) if raw and response.getheader('Content-Type', '').startswith('application/json') else raw
                    if status != 503 or not retry:
                        return status, value
                    with cls.stats_lock:
                        cls.backpressure_retries += 1
                except (ConnectionError, TimeoutError, http.client.RemoteDisconnected):
                    if not retry or not transport_safe:
                        raise
                    with cls.stats_lock:
                        cls.transport_retries += 1
            finally:
                conn.close()
            if time.monotonic() >= deadline:
                break
            time.sleep(min(.025 * (attempt + 1), .25))
        with cls.stats_lock:
            cls.retry_exhausted += 1
        raise AssertionError('bounded HTTP backpressure/transport retry budget exhausted')

    def signup(self):
        email, password = f'r0-qa-{uuid4()}@example.invalid', 'R0-synthetic-only-' + secrets.token_hex(10) + '!'
        request = urllib.request.Request(self.config['auth_url'] + '/signup', method='POST',
            headers={'Content-Type': 'application/json'},
            data=json.dumps({'email': email, 'password': password,
                             'data': {'role': 'service_role', 'organization_id': str(uuid4())}}).encode())
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        user = str(data.get('user', data)['id'])
        status, logged = self.http('POST', '/local/v1/auth/login', {'email': email, 'password': password})
        self.assertEqual(200, status, logged)
        return user, logged['access_token']

    def organization(self, user, *, max_count=128, max_bytes=16777216):
        org = str(uuid4())
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'independent synthetic QA')", (org,))
            db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')", (org, user))
            db.execute("INSERT INTO public.org_quotas(organization_id,max_jobs,max_active_jobs,max_duration_ms,max_bytes) VALUES(%s,64,2,10000,1048576)", (org,))
            db.execute("INSERT INTO g3a1_private.upload_limits(organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds) VALUES(%s,%s,%s,%s,120,60)",
                       (org, max_bytes, max_count, min(max_bytes, 1048576)))
        return org

    def setUp(self):
        self.case_started = time.monotonic()
        self.user, self.token = self.signup()
        self.org = self.organization(self.user)
        self.path = f'/v1/orgs/{self.org}/uploads'

    def tearDown(self):
        result = self._outcome.result
        failed = any(case.id().startswith(self.id()) for case, _ in result.failures + result.errors)
        type(self).case_evidence.append({'case': self._testMethodName, 'outcome': 'FAIL' if failed else 'PASS',
            'elapsed_seconds': round(time.monotonic() - self.case_started, 3)})

    def scalar(self, query, args=()):
        with psycopg.connect(self.admin_dsn) as db:
            return db.execute(query, args).fetchone()[0]

    def quota(self, org=None):
        with psycopg.connect(self.admin_dsn) as db:
            return db.execute('SELECT used_count,used_bytes FROM g3a1_private.upload_limits WHERE organization_id=%s', (org or self.org,)).fetchone()

    def reserve(self, payload, key=None, *, token=None, org=None, url=None):
        body = {'request_key': key or 'qa-' + uuid4().hex, 'size_bytes': len(payload), 'sha256': digest(payload)}
        status, grant = self.http('POST', f'/v1/orgs/{org or self.org}/uploads', body, token=token or self.token, url=url)
        self.assertEqual(201, status, grant)
        return body, grant

    def upload(self, payload, key=None):
        _, grant = self.reserve(payload, key)
        status, receipt = self.http('PUT', grant['upload_path'], payload, token=self.token, url=self.second_url)
        self.assertEqual(201, status, receipt)
        return receipt

    def approve(self, video=None, caption=None):
        video = self.video if video is None else video
        caption = self.caption if caption is None else caption
        with psycopg.connect(self.admin_dsn) as db:
            db.execute('INSERT INTO g3br0_private.synthetic_allowlist(organization_id,fixture_id,video_sha256,video_bytes,caption_sha256,caption_bytes,provenance) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (self.org, 'qa-' + uuid4().hex, digest(video), len(video), digest(caption), len(caption),
                 Jsonb({'generator': 'local synthetic QA', 'synthetic': True})))

    def request_ingest(self, video_receipt, caption_receipt, key=None):
        body = {'request_key': key or 'ingest-' + uuid4().hex,
                'video_receipt_id': video_receipt['id'], 'caption_receipt_id': caption_receipt['id']}
        status, request = self.http('POST', f'/local/v1/orgs/{self.org}/ingestions', body, token=self.token)
        self.assertEqual(202, status, request)
        return body, request

    def await_ingest(self, request_id):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            status, request = self.http('GET', f'/local/v1/orgs/{self.org}/ingestions/{request_id}', token=self.token)
            self.assertEqual(200, status, request)
            if request['state'] in {'completed', 'failed'}:
                return request
            time.sleep(.1)
        self.fail('ingest did not reach a terminal state within the real 30s operation deadline')

    @contextmanager
    def paused_worker(self):
        worker_pid = int(self.ready.get('worker_pid') or self.config['worker_pid'])
        os.kill(worker_pid, signal.SIGSTOP)
        try:
            yield
        finally:
            os.kill(worker_pid, signal.SIGCONT)

    def test_01_real_logout_and_expired_session_are_denied(self):
        self.assertEqual(200, self.http('GET', '/local/v1/me', token=self.token)[0])
        self.assertEqual(401, self.http('GET', '/local/v1/me')[0])
        self.assertEqual(200, self.http('POST', '/local/v1/auth/logout', {}, token=self.token)[0])
        # Signature/expiry remain valid: denial proves authoritative session check.
        claims = jwt.decode(self.token, self.config['jwt_secret'], algorithms=['HS256'],
                            issuer=self.config['issuer'], audience='authenticated')
        self.assertEqual(401, self.http('GET', '/local/v1/me', token=self.token, url=self.second_url)[0])
        _, fresh = self.signup()
        fresh_claims = jwt.decode(fresh, self.config['jwt_secret'], algorithms=['HS256'],
                                 issuer=self.config['issuer'], audience='authenticated')
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("UPDATE auth.sessions SET not_after=clock_timestamp()-interval '1 second' WHERE id=%s", (fresh_claims['session_id'],))
        self.assertEqual(401, self.http('GET', '/local/v1/me', token=fresh)[0])
        self.assertTrue(claims['session_id'])

    def test_02_cross_org_peer_receipt_and_revocation_fail_closed(self):
        payload = b'independent synthetic ownership probe'
        body, grant = self.reserve(payload)
        peer, peer_token = self.signup()
        peer_org = self.organization(peer)
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("INSERT INTO public.memberships VALUES(%s,%s,'editor')", (self.org, peer))
        self.assertIn(self.http('POST', self.path, body, token=peer_token)[0], (400, 403, 409))
        self.assertIn(self.http('PUT', grant['upload_path'], payload, token=peer_token)[0], (400, 403))
        self.assertEqual(403, self.http('POST', f'/v1/orgs/{peer_org}/uploads', body, token=self.token)[0])
        self.assertEqual(201, self.http('PUT', grant['upload_path'], payload, token=self.token)[0])
        self.assertIn(self.http('GET', self.path + '/' + grant['id'], token=peer_token)[0], (400, 403, 404))
        with psycopg.connect(self.admin_dsn) as db:
            db.execute('DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s', (self.org, self.user))
        self.assertEqual(403, self.http('POST', self.path, {'request_key': 'revoked', 'size_bytes': 1, 'sha256': 'a'*64}, token=self.token)[0])
        self.assertEqual((1, len(payload)), self.quota())

    def test_03_host_origin_framing_and_browser_promotion_denied(self):
        for headers in ({'Host': 'attacker.invalid'}, {'Origin': 'https://attacker.invalid'}, {'Origin': 'null'}):
            self.assertEqual(403, self.http('GET', '/', headers=headers)[0])
        target = urllib.parse.urlsplit(self.base_url)
        conn = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        try:
            conn.putrequest('GET', '/', skip_host=True)
            conn.putheader('Host', target.netloc)
            conn.putheader('Host', 'attacker.invalid')
            conn.endheaders()
            self.assertEqual(403, conn.getresponse().status)
        finally:
            conn.close()
        conn = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        try:
            conn.putrequest('POST', '/local/v1/auth/login')
            conn.putheader('Content-Length', '0')
            conn.putheader('Content-Length', '1')
            conn.endheaders()
            self.assertEqual(400, conn.getresponse().status)
        finally:
            conn.close()
        for path in ('/', '/app.js', '/styles.css'):
            status, content = self.http('GET', path)
            self.assertEqual(200, status)
            for privileged in ('admin_dsn', 'jwt_secret', 'signing_key', 'service_role_key'):
                self.assertNotIn(privileged.encode(), content)
        status, result = self.http('POST', f'/local/v1/orgs/{self.org}/ingestions',
            {'request_key': 'untrusted', 'video_receipt_id': str(uuid4()), 'caption_receipt_id': str(uuid4()),
             'rights_confirmed': True, 'asset_id': str(uuid4()), 'provenance': {'synthetic': True}}, token=self.token)
        self.assertEqual(400, status, result)
        self.assertEqual(0, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
        self.assertEqual(0, self.scalar('SELECT count(*) FROM g3br0_private.ingest_requests WHERE organization_id=%s', (self.org,)))
        with psycopg.connect(self.config['api_dsn']) as db:
            db.execute('SET LOCAL ROLE authenticated')
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (self.user,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute('INSERT INTO g3br0_private.synthetic_allowlist(organization_id) VALUES(%s)', (self.org,))

    def test_04_oversize_and_hash_mismatch_never_create_receipt(self):
        _, grant = self.reserve(b'good')
        self.assertEqual(400, self.http('PUT', grant['upload_path'], b'evil', token=self.token)[0])
        status, _ = self.http('PUT', grant['upload_path'], b'x' * 1048577, token=self.token)
        self.assertEqual(413, status)
        self.assertEqual(0, self.scalar('SELECT count(*) FROM g3a1_private.upload_receipts WHERE organization_id=%s', (self.org,)))
        self.assertEqual(0, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
        self.assertEqual((1, 4), self.quota())

    def test_05_invalid_video_srt_and_unapproved_pair_cannot_be_promoted(self):
        pairs = [(b'not an mp4 synthetic bytes', self.caption, True),
                 (self.video, b'not valid SRT synthetic bytes', True),
                 (self.video, self.caption + b'\n', False)]
        for video, caption, approved in pairs:
            with self.subTest(approved=approved, video_valid=video == self.video):
                if approved:
                    self.approve(video, caption)
                v, s = self.upload(video), self.upload(caption)
                _, pending = self.request_ingest(v, s)
                result = self.await_ingest(pending['id'])
                self.assertEqual('failed', result['state'], result)
                self.assertIsNone(result['asset_id'])
                self.assertEqual(0, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
        self.assertEqual((6, sum(len(v)+len(s) for v,s,_ in pairs)), self.quota())

    def test_06_100_duplicate_reservations_and_20_distinct_quota_race(self):
        evidence = {'probe': 'upload_admission', 'state': 'incomplete',
                    'logical_duplicate_requests': 100, 'logical_distinct_requests': 20,
                    'caller_concurrency': 8, 'independent_http_replicas': 2}
        type(self).workloads.append(evidence)
        payload = b'synthetic concurrent upload quota bytes'
        body = {'request_key': 'same-key-100', 'size_bytes': len(payload), 'sha256': digest(payload)}
        def reserve_one(i):
            return self.http('POST', self.path, body, token=self.token,
                             url=self.base_url if i % 2 else self.second_url)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as pool:
            duplicate = list(pool.map(reserve_one, range(100)))
        evidence.update(duplicate_responses=len(duplicate),
                        duplicate_statuses={str(status): sum(s == status for s,_ in duplicate) for status,_ in duplicate},
                        duplicate_reservation_ids=len({r.get('id') for _,r in duplicate}),
                        duplicate_quota=list(self.quota()))
        self.assertEqual({201}, {s for s,_ in duplicate})
        self.assertEqual(1, len({r['id'] for _,r in duplicate}))
        self.assertEqual((1, len(payload)), self.quota())
        org = self.organization(self.user, max_count=10, max_bytes=10*len(payload))
        def distinct_one(i):
            return self.http('POST', f'/v1/orgs/{org}/uploads', {**body, 'request_key': 'distinct-' + str(i)},
                             token=self.token, url=self.base_url if i % 2 else self.second_url)
        with ThreadPoolExecutor(max_workers=8) as pool:
            distinct = list(pool.map(distinct_one, range(20)))
        evidence.update(distinct_responses=len(distinct), distinct_admitted=sum(s == 201 for s,_ in distinct),
                        distinct_quota_denied=sum(s == 429 for s,_ in distinct), distinct_quota=list(self.quota(org)),
                        elapsed_seconds=round(time.monotonic()-started, 3))
        self.assertEqual(10, sum(s == 201 for s,_ in distinct), distinct)
        self.assertEqual(10, sum(s == 429 for s,_ in distinct), distinct)
        self.assertEqual((10, 10*len(payload)), self.quota(org))
        evidence['state'] = 'assertions_passed'
        print(json.dumps({'probe': '100_duplicate_plus_20_distinct', 'logical_requests': 120,
                          'elapsed_seconds': round(time.monotonic()-started, 3),
                          'duplicate_reservations': 1, 'distinct_admitted': 10, 'distinct_quota_denied': 10}))

    def test_07_lost_upload_response_and_100_ingest_render_replays(self):
        evidence = {'probe': 'ingest_render_replay', 'state': 'incomplete',
                    'initial_ingest_requests': 1, 'logical_ingest_replays': 100,
                    'logical_render_requests': 100, 'caller_concurrency': 8,
                    'independent_http_replicas': 2, 'response_abandoned_on_real_socket': False}
        type(self).workloads.append(evidence)
        self.approve()
        v, s = self.upload(self.video), self.upload(self.caption)
        # Abandon a real successful duplicate-reserve response, then replay.
        body = {'request_key': 'lost-response', 'size_bytes': 8, 'sha256': digest(b'testdata')}
        self.assertEqual(201, self.http('POST', self.path, body, token=self.token, lose_response=True)[0])
        evidence['response_abandoned_on_real_socket'] = True
        status, replay = self.http('POST', self.path, body, token=self.token, url=self.second_url)
        self.assertEqual(201, status, replay)
        self.assertEqual(1, self.scalar("SELECT count(*) FROM g3a1_private.upload_reservations WHERE organization_id=%s AND request_key='lost-response'", (self.org,)))
        ingest_body, pending = self.request_ingest(v, s, 'same-ingest-100')
        def ingest_one(i):
            return self.http('POST', f'/local/v1/orgs/{self.org}/ingestions', ingest_body,
                             token=self.token, url=self.base_url if i%2 else self.second_url)
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(ingest_one, range(100)))
        evidence.update(ingest_responses=len(responses), ingest_ids=len({value.get('id') for _,value in responses}),
                        ingest_statuses={str(status): sum(s == status for s,_ in responses) for status,_ in responses})
        self.assertEqual({202}, {status for status,_ in responses})
        self.assertEqual({pending['id']}, {value['id'] for _,value in responses})
        complete = self.await_ingest(pending['id'])
        self.assertEqual('completed', complete['state'], complete)
        render_body = {'request_key': 'same-render-100', 'asset_id': complete['asset_id'],
                       'duration_ms': self.fixture['duration_ms'], 'quality': 'smoke'}
        def render_one(i):
            return self.http('POST', f'/v1/orgs/{self.org}/renders', render_body,
                             token=self.token, url=self.base_url if i%2 else self.second_url)
        with ThreadPoolExecutor(max_workers=8) as pool:
            renders = list(pool.map(render_one, range(100)))
        evidence.update(render_responses=len(renders), render_ids=len({value.get('id') for _,value in renders}),
                        render_statuses={str(status): sum(s == status for s,_ in renders) for status,_ in renders},
                        assets=self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)),
                        jobs=self.scalar('SELECT count(*) FROM public.render_jobs WHERE organization_id=%s', (self.org,)),
                        outbox_events=self.scalar('SELECT count(*) FROM public.render_outbox o JOIN public.render_jobs j ON j.id=o.job_id WHERE j.organization_id=%s', (self.org,)))
        self.assertEqual({202}, {status for status,_ in renders}, renders)
        self.assertEqual(1, len({result['id'] for _,result in renders}))
        self.assertEqual(1, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
        self.assertEqual(1, self.scalar('SELECT count(*) FROM public.render_jobs WHERE organization_id=%s', (self.org,)))
        self.assertEqual(1, self.scalar('SELECT count(*) FROM public.render_outbox o JOIN public.render_jobs j ON j.id=o.job_id WHERE j.organization_id=%s', (self.org,)))
        self.assertEqual((3, len(self.video)+len(self.caption)+8), self.quota())
        job_id = renders[0][1]['id']
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status, job = self.http('GET', f'/v1/orgs/{self.org}/renders/{job_id}', token=self.token)
            self.assertEqual(200, status, job)
            if job['state'] in {'completed', 'failed'}:
                break
            time.sleep(.2)
        self.assertEqual('completed', job['state'], job)
        status, manifest = self.http('GET', f'/local/v1/orgs/{self.org}/renders/{job_id}/manifest', token=self.token)
        self.assertEqual(200, status, manifest)
        self.assertEqual({'vertical', 'square', 'feed'}, {o['preset'] for o in manifest['outputs']})
        self.assertEqual('supplied-srt-burned', manifest['caption_mode'])
        for output in manifest['outputs']:
            status, grant = self.http('POST', f'/v1/orgs/{self.org}/renders/{job_id}/downloads',
                                     {'preset': output['preset']}, token=self.token)
            self.assertEqual(200, status, grant)
            status, exported = self.http('GET', grant['download_path'], token=self.token, url=self.second_url)
            self.assertEqual(200, status)
            self.assertEqual((output['size_bytes'], output['sha256']), (len(exported), digest(exported)))
        evidence.update(state='assertions_passed', verified_private_downloads=len(manifest['outputs']))

    def test_08_ingester_sigkill_durable_objects_recovery_and_stale_fence(self):
        evidence = {'probe': 'ingester_sigkill', 'state': 'incomplete', 'destructive_gc': False}
        type(self).workloads.append(evidence)
        self.approve()
        v, s = self.upload(self.video), self.upload(self.caption)
        with self.paused_worker():
            _, pending = self.request_ingest(v, s)
            checkpoint = self.temp_root / ('checkpoint-' + uuid4().hex)
            driver = '''import json,os,signal,sys\nfrom pathlib import Path\nfrom engine.local_ingest import LocalIngestWorker\nfrom engine.object_store import LocalObjectStore\nc=json.loads(Path(os.environ['G3BR0_TEST_CONFIG']).read_text())\ndef hook(stage,claim):\n if stage=='after_publish':\n  Path(sys.argv[1]).write_text(str(claim['id']))\n  signal.pause()\nw=LocalIngestWorker(c['ingest_dsn'],LocalObjectStore(c['store_root']),scratch_root=Path(c['ingest_scratch']),lease_seconds=2,fault_hook=hook)\nw.process_one()\n'''
            child = subprocess.Popen([sys.executable, '-c', driver, str(checkpoint)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+15
                while not checkpoint.exists():
                    if child.poll() is not None:
                        self.fail('real ingester exited before durable-object checkpoint: ' + child.stderr.read().decode()[-2000:])
                    if time.monotonic()>deadline:
                        self.fail('real ingester did not reach durable-object checkpoint')
                    time.sleep(.05)
                self.assertEqual(pending['id'], checkpoint.read_text())
                with psycopg.connect(self.admin_dsn, row_factory=dict_row) as db:
                    old = db.execute('SELECT * FROM g3br0_private.ingest_requests WHERE id=%s', (pending['id'],)).fetchone()
                self.assertEqual('validating', old['state'])
                self.assertEqual(0, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
                child.kill()  # Actual SIGKILL after persistent object publication.
                child.wait(timeout=5)
                self.assertEqual(-signal.SIGKILL, child.returncode)
                evidence.update(signal='SIGKILL', returncode=child.returncode, checkpoint='after_publish',
                                fence_before=old['fence'])
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                child.stderr.close()
            time.sleep(2.2)  # Natural test lease expiry; no database-clock mutation.
            from engine.local_ingest import LocalIngestWorker
            from engine.object_store import LocalObjectStore
            recovered = LocalIngestWorker(self.config['ingest_dsn'], LocalObjectStore(self.config['store_root']),
                scratch_root=Path(self.config['ingest_scratch']), lease_seconds=2).process_one()
            self.assertEqual('completed', recovered['state'], recovered)
            with psycopg.connect(self.admin_dsn, row_factory=dict_row) as db:
                current = db.execute('SELECT * FROM g3br0_private.ingest_requests WHERE id=%s', (pending['id'],)).fetchone()
            self.assertGreater(current['fence'], old['fence'])
            with psycopg.connect(self.config['ingest_dsn']) as db:
                db.execute('SET LOCAL ROLE g3br0_ingester')
                with self.assertRaises(psycopg.Error) as caught:
                    db.execute('SELECT g3br0_private.complete_ingest(%s,%s,%s,%s)',
                        (pending['id'], old['lease_token'], old['fence'], Jsonb(current['completed_metadata'])))
                self.assertEqual('55000', caught.exception.sqlstate)
                evidence.update(stale_completion_sqlstate=caught.exception.sqlstate, fence_after=current['fence'])
            self.assertEqual((2, len(self.video)+len(self.caption)), self.quota())
            self.assertEqual(1, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
            evidence.update(state='assertions_passed', quota_retained=True, authoritative_assets=1)

    def test_09_render_sigkill_fenced_recovery_and_orphan_reconciliation(self):
        evidence = {'probe': 'renderer_sigkill', 'state': 'incomplete', 'destructive_gc': False}
        type(self).workloads.append(evidence)
        self.approve()
        _, pending = self.request_ingest(self.upload(self.video), self.upload(self.caption))
        asset = self.await_ingest(pending['id'])
        self.assertEqual('completed', asset['state'], asset)
        with self.paused_worker():
            status, job = self.http('POST', f'/v1/orgs/{self.org}/renders',
                {'request_key': 'kill-render', 'asset_id': asset['asset_id'],
                 'duration_ms': self.fixture['duration_ms'], 'quality': 'smoke'}, token=self.token)
            self.assertEqual(202, status, job)
            checkpoint = self.temp_root / ('render-checkpoint-' + uuid4().hex)
            driver = '''import json,os,signal,sys\nfrom pathlib import Path\nfrom engine.media_pipeline import MediaWorker\nfrom engine.object_store import LocalObjectStore\nc=json.loads(Path(os.environ['G3BR0_TEST_CONFIG']).read_text())\ndef hook(stage,claim):\n if stage=='after_object':\n  Path(sys.argv[1]).write_text(str(claim['id']))\n  signal.pause()\nw=MediaWorker(c['worker_dsn'],LocalObjectStore(c['store_root']),Path(c['scratch_root']),lease_seconds=2,fault_hook=hook)\nw.run(sys.argv[2])\n'''
            child = subprocess.Popen([sys.executable, '-c', driver, str(checkpoint), job['id']],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+120
                while not checkpoint.exists():
                    if child.poll() is not None:
                        self.fail('real renderer exited before persistent-object checkpoint: ' + child.stderr.read().decode()[-2000:])
                    if time.monotonic()>deadline:
                        self.fail('real renderer did not reach persistent-object checkpoint')
                    time.sleep(.1)
                with psycopg.connect(self.admin_dsn, row_factory=dict_row) as db:
                    old = db.execute('SELECT * FROM public.render_jobs WHERE id=%s', (job['id'],)).fetchone()
                self.assertEqual('running', old['state'])
                self.assertIsNone(old['output_manifest'])
                child.kill()
                child.wait(timeout=5)
                self.assertEqual(-signal.SIGKILL, child.returncode)
                evidence.update(signal='SIGKILL', returncode=child.returncode, checkpoint='after_object',
                                fence_before=old['fence'])
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stderr.close()
            time.sleep(2.2)
            from engine.control_plane import StaleLease
            from engine.media_pipeline import MediaWorker
            from engine.object_store import LocalObjectStore
            store = LocalObjectStore(self.config['store_root'])
            worker = MediaWorker(self.config['worker_dsn'], store, Path(self.config['scratch_root']), lease_seconds=5)
            recovered = worker.run(job['id'])
            self.assertEqual('completed', recovered['state'], recovered)
            self.assertGreater(recovered['fence'], old['fence'])
            with self.assertRaises(StaleLease):
                worker.control.finish_job(job['id'], old['lease_token'], old['fence'], recovered['output_manifest'])
            report = worker.reconcile_orphans(self.org, job['id'])
            self.assertGreaterEqual(len(report['objects']), 1, report)
            self.assertFalse(report['deleted'])
            # R0 inventories obsolete files conservatively. Destructive garbage
            # collection is outside this gate; retain every detected fragment.
            self.assertEqual(report['objects'], worker.reconcile_orphans(self.org, job['id'])['objects'])
            for key in report['objects']:
                self.assertGreater(store.stat(self.org, key)['size_bytes'], 0)
            for output in recovered['output_manifest']['outputs']:
                self.assertEqual(output['sha256'], store.stat(self.org, output['key'])['sha256'])
            self.assertEqual((2, len(self.video)+len(self.caption)), self.quota())
            evidence.update(state='assertions_passed', stale_completion='StaleLease', fence_after=recovered['fence'],
                            obsolete_objects_retained=len(report['objects']), quota_retained=True,
                            authoritative_outputs_verified=len(recovered['output_manifest']['outputs']))

    def test_10_receipt_readback_detects_corrupted_private_object(self):
        self.approve()
        v, s = self.upload(self.video), self.upload(self.caption)
        with self.paused_worker():
            # Deliberate disk fault injected by the privileged disposable test
            # driver. Browser and API principals cannot write this path.
            with psycopg.connect(self.admin_dsn) as db:
                key = db.execute('SELECT object_key FROM g3a1_private.upload_receipts WHERE id=%s', (v['id'],)).fetchone()[0]
            object_path = Path(self.config['store_root']) / key
            original = object_path.read_bytes()
            object_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            _, pending = self.request_ingest(v, s)
        result = self.await_ingest(pending['id'])
        self.assertEqual('failed', result['state'], result)
        self.assertEqual('integrity_mismatch', result['error_code'])
        self.assertEqual(0, self.scalar('SELECT count(*) FROM public.assets WHERE organization_id=%s', (self.org,)))
        self.assertEqual((2, len(self.video)+len(self.caption)), self.quota())


if __name__ == '__main__':
    unittest.main()
