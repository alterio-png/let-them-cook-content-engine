#!/usr/bin/env bash
# Official Auth component integration only. No hosted Supabase or cloud access.
set -euo pipefail
if [ -n "${PGSERVICE:-}" ] || [ -n "${PGHOSTADDR:-}" ]; then
 echo 'Refusing libpq service/host-address overrides in local Auth laboratory' >&2
 exit 1
fi
cd /workspace/issue12-pr9-clean
python_bin="${G3A1_PYTHON:-python3}"
postgres_image='postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9'
auth_image='supabase/gotrue@sha256:839f529492d116b4e8b7777c953a27c381d34c15a744b1bcefde5eefaa1f9f9f'
run_id="g3a1-auth-${$}-${RANDOM}"
network_name="${run_id}-net"
postgres_name="${run_id}-pg"
auth_name="${run_id}-auth"
export G3A_AUTH_DB_PASSWORD="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(32))')"
export G3A_AUTH_JWT_SECRET="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(48))')"
export G3A_API_PASSWORD="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(32))')"
export G3A_AUTH_ISSUER='http://g3a-auth.invalid/auth/v1'
db_name="g3a_auth_${$}_${RANDOM}"
# Avoid rootless Docker's allocator colliding with outbound ephemeral sockets.
# Bind probes are released immediately before publication; failure is fail-closed.
read -r pg_port auth_port < <("$python_bin" - <<'PY'
import secrets,socket
ports=[]
while len(ports)<2:
    candidate=12000+secrets.randbelow(12000)
    if candidate in ports: continue
    try:
        with socket.socket() as s: s.bind(('127.0.0.1',candidate))
    except OSError: continue
    ports.append(candidate)
print(*ports)
PY
)
cleanup() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        printf 'Official Auth integration failed (exit %s). Resource state:\n' "$result" >&2
        docker ps -a --filter "name=${run_id}" --format '{{.Names}} {{.Status}}' >&2 || true
        # Auth logs can contain request identities. Do not print tokens or env.
        docker logs --tail 25 "$auth_name" 2>&1 | sed -E 's/(access_token|refresh_token|password|secret)"?:[^,}]*/\1:[REDACTED]/g' >&2 || true
    fi
    docker rm -fv "$auth_name" "$postgres_name" >/dev/null 2>&1 || true
    docker network rm "$network_name" >/dev/null 2>&1 || true
    exit "$result"
}
trap cleanup EXIT
# Rootless Docker does not publish ports on --internal bridges. A dedicated
# bridge with masquerading disabled keeps this runner portable and loopback
# publications private; this is not claimed as a verified egress firewall.
docker network create --opt com.docker.network.bridge.enable_ip_masquerade=false "$network_name" >/dev/null
docker run -d --name "$postgres_name" --network "$network_name" --network-alias postgres \
    --cpus 2 --memory 512m --pids-limit 128 \
    -p "127.0.0.1:${pg_port}:5432" -e POSTGRES_USER=postgres \
    -e "POSTGRES_PASSWORD=$G3A_AUTH_DB_PASSWORD" -e "POSTGRES_DB=$db_name" \
    "$postgres_image" >/dev/null
export G3A_AUTH_ADMIN_DSN="postgresql://postgres:${G3A_AUTH_DB_PASSWORD}@127.0.0.1:${pg_port}/${db_name}"
export G3A_API_DSN="postgresql://g3a_api_login:${G3A_API_PASSWORD}@127.0.0.1:${pg_port}/${db_name}"
for attempt in $(seq 1 60); do
    if docker exec "$postgres_name" pg_isready -h 127.0.0.1 -p 5432 -U postgres >/dev/null 2>&1; then break; fi
    sleep 0.5
done
"$python_bin" scripts/prepare_auth_database.py bootstrap
docker run -d --name "$auth_name" --network "$network_name" \
    --cpus 1 --memory 256m --pids-limit 128 --cap-drop ALL \
    --security-opt no-new-privileges --read-only --tmpfs /tmp:size=16m,noexec,nosuid \
    -p "127.0.0.1:${auth_port}:9999" \
    -e GOTRUE_API_HOST=0.0.0.0 -e PORT=9999 \
    -e "API_EXTERNAL_URL=$G3A_AUTH_ISSUER" -e "GOTRUE_JWT_ISSUER=$G3A_AUTH_ISSUER" \
    -e GOTRUE_DB_DRIVER=postgres -e DB_NAMESPACE=auth \
    -e "DATABASE_URL=postgresql://supabase_auth_admin:${G3A_AUTH_DB_PASSWORD}@postgres:5432/${db_name}" \
    -e "GOTRUE_JWT_SECRET=$G3A_AUTH_JWT_SECRET" -e GOTRUE_JWT_EXP=600 \
    -e GOTRUE_JWT_AUD=authenticated -e GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated \
    -e GOTRUE_DISABLE_SIGNUP=false -e GOTRUE_EXTERNAL_EMAIL_ENABLED=true \
    -e GOTRUE_EXTERNAL_PHONE_ENABLED=false -e GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=false \
    -e GOTRUE_SITE_URL=http://127.0.0.1 -e GOTRUE_MAILER_AUTOCONFIRM=true \
    -e GOTRUE_RATE_LIMIT_EMAIL_SENT=1000 -e GOTRUE_RATE_LIMIT_TOKEN_REFRESH=1000 \
    "$auth_image" >/dev/null
export G3A_AUTH_URL="http://127.0.0.1:${auth_port}"
"$python_bin" - <<'PY'
import os,time,urllib.request
for attempt in range(120):
    try:
        with urllib.request.urlopen(os.environ['G3A_AUTH_URL']+'/health',timeout=1) as r:
            if r.status == 200: break
    except Exception:
        time.sleep(0.25)
else:
    raise SystemExit('BLOCKED: official Auth health did not become ready')
PY
"$python_bin" scripts/prepare_auth_database.py engine
"$python_bin" scripts/prepare_g3a1_auth_database.py
printf 'Official Supabase Auth component v2.197.0 + PostgreSQL 16.15, isolated local integration\n'
PYTHONPATH=.:integration:media_tests:cloud_tests:upload_tests "$python_bin" /workspace/issue12-evidence/http-review/probe_official.py
