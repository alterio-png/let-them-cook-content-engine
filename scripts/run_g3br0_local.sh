#!/usr/bin/env bash
# Real, isolated, local PostgreSQL + GoTrue + browser API + trusted media worker.
# No cloud credentials, deployment or existing database is accepted here.
set -euo pipefail
umask 077
export NO_PROXY='127.0.0.1,localhost,::1'
export no_proxy="$NO_PROXY"
cd "$(dirname "$0")/.."
if [ -n "${PGSERVICE:-}" ] || [ -n "${PGHOSTADDR:-}" ]; then
    echo 'Refusing libpq overrides in the disposable local laboratory' >&2
    exit 1
fi
python_bin="${G3BR0_PYTHON:-python3}"
test_mode=false
prepare_tests=false
output=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --test) test_mode=true; shift ;;
        --prepare-tests) prepare_tests=true; shift ;;
        --output) output="${2:?--output requires a new directory}"; shift 2 ;;
        *) echo 'Usage: scripts/run_g3br0_local.sh [--test|--prepare-tests] --output NEW_PRIVATE_DIRECTORY' >&2; exit 2 ;;
    esac
done
[ -n "$output" ] || { echo '--output is required' >&2; exit 2; }
output="$($python_bin -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).absolute())' "$output")"
"$python_bin" - "$output" "$PWD" <<'PY'
import os,pathlib,sys
target,checkout=map(pathlib.Path,sys.argv[1:])
if target.exists() or target.is_symlink() or target==checkout or checkout in target.parents:
    raise SystemExit('Choose a new evidence directory outside the checkout')
target.mkdir(mode=0o700,parents=True)
os.chmod(target,0o700)
PY
docker_local() {
    env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
        docker --host=unix:///var/run/docker.sock "$@"
}
component() {
    # Setup/test credentials never enter a serving component's environment.
    # Each child receives only its dedicated private configuration path.
    exec env -u G3A_AUTH_ADMIN_DSN -u G3A_AUTH_DB_PASSWORD -u G3A_API_PASSWORD \
        -u G3A_AUTH_JWT_SECRET -u G3A_API_DSN -u G1_TEST_DSN \
        -u G3BR0_TEST_CONFIG -u G3BR0_READY_FILE "$@"
}
docker_local info >/dev/null
postgres_image='postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9'
auth_image='supabase/gotrue@sha256:839f529492d116b4e8b7777c953a27c381d34c15a744b1bcefde5eefaa1f9f9f'
run_id="g3br0-${$}-${RANDOM}"
network_name="${run_id}-net"
postgres_name="${run_id}-pg"
auth_name="${run_id}-auth"
api_pid=''; worker_pid=''; monitor_pid=''
keep_database=false
export G3A_AUTH_DB_PASSWORD="$($python_bin -c 'import secrets; print(secrets.token_urlsafe(32))')"
export G3A_AUTH_JWT_SECRET="$($python_bin -c 'import secrets; print(secrets.token_urlsafe(48))')"
export G3A_API_PASSWORD="$($python_bin -c 'import secrets; print(secrets.token_urlsafe(32))')"
export G3A_AUTH_ISSUER='http://g3a-auth.invalid/auth/v1'
db_name="g3a_auth_r0_${$}_${RANDOM}"
read -r pg_port auth_port api_port < <("$python_bin" - <<'PY'
import secrets,socket
ports=[]
while len(ports)<3:
    candidate=12000+secrets.randbelow(12000)
    if candidate in ports: continue
    try:
        with socket.socket() as probe: probe.bind(('127.0.0.1',candidate))
    except OSError: continue
    ports.append(candidate)
print(*ports)
PY
)
export G3A_AUTH_ADMIN_DSN="postgresql://postgres:${G3A_AUTH_DB_PASSWORD}@127.0.0.1:${pg_port}/${db_name}"
export G3A_API_DSN="postgresql://g3a_api_login:${G3A_API_PASSWORD}@127.0.0.1:${pg_port}/${db_name}"
export G3A_AUTH_URL="http://127.0.0.1:${auth_port}"
export G3BR0_READY_FILE="$output/public-ready.json"
export G3BR0_TEST_CONFIG="$output/test-config.json"
export PYTHONPATH="${PWD}:${PWD}/integration:${PWD}/media_tests:${PWD}/cloud_tests:${PWD}/upload_tests${PYTHONPATH:+:${PYTHONPATH}}"

stop_child() {
    local child="$1" bound="$2"
    [ -n "$child" ] || return 0
    kill -CONT "$child" 2>/dev/null || true
    kill -TERM "$child" 2>/dev/null || true
    for ((step=0; step<bound*4; step++)); do
        kill -0 "$child" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$child" 2>/dev/null; then
        keep_database=true
        kill -KILL "$child" 2>/dev/null || true
    fi
    wait "$child" 2>/dev/null || true
}
cleanup() {
    result=$?
    trap - EXIT INT TERM
    # Stop admissions before draining the one bounded physical worker. TERM
    # does not cancel in-flight native work; the historical 120s cap still acts.
    stop_child "$api_pid" 10
    stop_child "$worker_pid" 130
    stop_child "$monitor_pid" 5
    if docker_local inspect "$postgres_name" >/dev/null 2>&1; then
        if [ -f "$output/worker-config.json" ]; then
            "$python_bin" -m engine.local_runtime reconcile --config "$output/worker-config.json" \
                >"$output/reconciliation-private.json" 2>"$output/reconciliation-private.log" || keep_database=true
        fi
        if ! "$python_bin" - "$output" <<'PY'
import json,os,pathlib,psycopg,sys
out=pathlib.Path(sys.argv[1])
try:
    with psycopg.connect(os.environ['G3A_AUTH_ADMIN_DSN'],connect_timeout=3) as db:
        db.execute("SET LOCAL statement_timeout='3s'")
        rows={}
        for table in ('public.render_jobs','g3br0_private.ingest_requests','g3a1_private.upload_reservations'):
            if db.execute('SELECT to_regclass(%s)',(table,)).fetchone()[0]:
                rows[table]=dict(db.execute('SELECT state,count(*) FROM '+table+' GROUP BY state').fetchall())
        unresolved=any(count for table,states in rows.items() for state,count in states.items()
                       if state not in {'completed','failed','uploaded','expired'})
        (out/'teardown-state.json').write_text(json.dumps({'states':rows,'unresolved':unresolved},indent=2)+'\n')
        os.chmod(out/'teardown-state.json',0o600)
except Exception as error:
    (out/'teardown-state.json').write_text(json.dumps({'unresolved':True,'error_code':type(error).__name__})+'\n')
    unresolved=True
raise SystemExit(1 if unresolved else 0)
PY
        then keep_database=true; fi
        # Private database snapshot is intentionally excluded from publishable
        # evidence: it contains local Auth identities and password hashes.
        docker_local exec "$postgres_name" pg_dump -U postgres -d "$db_name" \
            >"$output/database-private.sql" 2>"$output/database-dump-private.log" || keep_database=true
    fi
    if "$keep_database"; then
        docker_local stop -t 5 "$auth_name" "$postgres_name" >/dev/null 2>&1 || true
        "$python_bin" - "$output/retained-resources.json" "$postgres_name" "$auth_name" "$network_name" <<'PY'
import json,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({'postgres':sys.argv[2],'auth':sys.argv[3],'network':sys.argv[4],
 'reason':'nonterminal or unobserved durable state; explicit reconciliation required'},indent=2)+'\n')
PY
        echo "Nonterminal state retained in stopped owned containers; details: $output/retained-resources.json" >&2
    else
        docker_local rm -fv "$auth_name" "$postgres_name" >/dev/null 2>&1 || true
        docker_local network rm "$network_name" >/dev/null 2>&1 || true
    fi
    echo "Private local evidence retained: $output"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Dedicated bridge and loopback publications. Disabling masquerade is not
# presented as a verified egress firewall (including rootless Docker).
docker_local network create --opt com.docker.network.bridge.enable_ip_masquerade=false "$network_name" >/dev/null
docker_local run -d --name "$postgres_name" --network "$network_name" --network-alias postgres \
    --cpus 2 --memory 512m --pids-limit 128 -p "127.0.0.1:${pg_port}:5432" \
    -e POSTGRES_USER=postgres -e "POSTGRES_PASSWORD=$G3A_AUTH_DB_PASSWORD" -e "POSTGRES_DB=$db_name" \
    "$postgres_image" -c max_connections=30 -c superuser_reserved_connections=3 >/dev/null
for attempt in $(seq 1 120); do
    docker_local exec "$postgres_name" pg_isready -h 127.0.0.1 -p 5432 -U postgres >/dev/null 2>&1 && break
    sleep 0.25
done
"$python_bin" scripts/prepare_auth_database.py bootstrap >"$output/bootstrap.log" 2>&1
# Set the server-enforced Auth role backstop before its first GoTrue connection.
"$python_bin" - <<'PY'
import os,psycopg
with psycopg.connect(os.environ['G3A_AUTH_ADMIN_DSN']) as db:
    db.execute('ALTER ROLE supabase_auth_admin CONNECTION LIMIT 4')
PY
docker_local run -d --name "$auth_name" --network "$network_name" \
    --cpus 1 --memory 256m --pids-limit 128 --cap-drop ALL \
    --security-opt no-new-privileges --read-only --tmpfs /tmp:size=16m,noexec,nosuid \
    -p "127.0.0.1:${auth_port}:9999" -e GOTRUE_API_HOST=0.0.0.0 -e PORT=9999 \
    -e "API_EXTERNAL_URL=$G3A_AUTH_ISSUER" -e "GOTRUE_JWT_ISSUER=$G3A_AUTH_ISSUER" \
    -e GOTRUE_DB_DRIVER=postgres -e DB_NAMESPACE=auth -e GOTRUE_DB_MAX_POOL_SIZE=4 \
    -e "DATABASE_URL=postgresql://supabase_auth_admin:${G3A_AUTH_DB_PASSWORD}@postgres:5432/${db_name}" \
    -e "GOTRUE_JWT_SECRET=$G3A_AUTH_JWT_SECRET" -e GOTRUE_JWT_EXP=600 \
    -e GOTRUE_JWT_AUD=authenticated -e GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated \
    -e GOTRUE_DISABLE_SIGNUP=false -e GOTRUE_EXTERNAL_EMAIL_ENABLED=true \
    -e GOTRUE_EXTERNAL_PHONE_ENABLED=false -e GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=false \
    -e GOTRUE_SITE_URL=http://127.0.0.1 -e GOTRUE_MAILER_AUTOCONFIRM=true \
    -e GOTRUE_RATE_LIMIT_EMAIL_SENT=1000 -e GOTRUE_RATE_LIMIT_TOKEN_REFRESH=1000 \
    "$auth_image" >/dev/null
"$python_bin" - <<'PY'
import os,time,urllib.request
for attempt in range(120):
    try:
        with urllib.request.urlopen(os.environ['G3A_AUTH_URL']+'/health',timeout=1) as response:
            if response.status==200: break
    except Exception: time.sleep(.25)
else: raise SystemExit('BLOCKED: official local Auth did not become healthy')
PY
"$python_bin" scripts/prepare_auth_database.py engine >>"$output/bootstrap.log" 2>&1
"$python_bin" scripts/prepare_g3a1_auth_database.py >>"$output/bootstrap.log" 2>&1
prepare_args=(--output "$output" --port "$api_port" --parent-pid "$$")
if "$test_mode" || "$prepare_tests"; then
    fixture_db="g1_r0_${$}_${RANDOM}"
    export G1_TEST_DSN="postgresql://postgres:${G3A_AUTH_DB_PASSWORD}@127.0.0.1:${pg_port}/${fixture_db}"
    prepare_args+=(--fixture-database "$fixture_db")
fi
"$python_bin" scripts/prepare_g3br0_lab.py "${prepare_args[@]}" >>"$output/bootstrap.log" 2>&1
component "$python_bin" -m engine.local_browser_api --config "$output/api-config.json" --ready-file "$output/api-ready.json" \
    >"$output/logs/api-process.log" 2>&1 &
api_pid=$!
component "$python_bin" -m engine.local_runtime worker --config "$output/worker-config.json" >"$output/logs/worker-process.log" 2>&1 &
worker_pid=$!
component "$python_bin" -m engine.local_runtime monitor --config "$output/monitor-config.json" >"$output/logs/monitor-process.log" 2>&1 &
monitor_pid=$!
"$python_bin" - "$output" "$api_pid" "$worker_pid" "$monitor_pid" <<'PY'
import json,os,pathlib,sys,time,urllib.request
out=pathlib.Path(sys.argv[1])
for attempt in range(120):
    try:
        ready=json.loads((out/'api-ready.json').read_text())
        with urllib.request.urlopen(ready['url']+'/health',timeout=1) as response:
            if response.status==200: break
    except Exception: time.sleep(.25)
else: raise SystemExit('BLOCKED: local browser API did not become ready')
for name in ('public-ready.json','test-config.json'):
    path=out/name; value=json.loads(path.read_text())
    value.update(api_pid=int(sys.argv[2]),worker_pid=int(sys.argv[3]),monitor_pid=int(sys.argv[4]),output_dir=str(out))
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');path.chmod(0o600)
PY
echo "Browser local: http://127.0.0.1:${api_port}"
echo "Private demo credentials and fixtures: $G3BR0_READY_FILE"
if "$test_mode"; then
    "$python_bin" -m unittest discover -s local_tests/r0 -v >"$output/local-tests.log" 2>&1
    node browser_tests/r0_e2e.mjs --ready "$G3BR0_READY_FILE" --output "$output/browser" >"$output/browser-tests.log" 2>&1
    echo 'Local integration and real browser commands completed; inspect their evidence before declaring PASS.'
else
    echo 'Foreground local laboratory; Ctrl-C stops admissions and drains bounded work.'
    # A dead API/daemon fails the foreground supervisor instead of silently
    # serving an incomplete demonstration indefinitely.
    wait -n "$api_pid" "$worker_pid" "$monitor_pid"
    echo 'A local component stopped unexpectedly' >&2
    exit 1
fi
