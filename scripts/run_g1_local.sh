#!/bin/sh
# Disposable local proof only. Requires Docker and Python 3.12 Linux x86_64.
set -eu
cd "$(dirname "$0")/.."
G1_PYTHON=${G1_PYTHON:-python3}
G1_PORT=${G1_PORT:-55432}
G1_CONTAINER="ltc-g1-$$"
G1_IMAGE='postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9'
cleanup() { docker rm -f "$G1_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
docker run -d --name "$G1_CONTAINER" -p "127.0.0.1:$G1_PORT:5432" \
  -e POSTGRES_PASSWORD=g1-local-disposable -e POSTGRES_DB=g1_test \
  --tmpfs /var/lib/postgresql/data:rw,size=512m "$G1_IMAGE" \
  -c max_connections=160 >/dev/null
G1_READY=0
for attempt in $(seq 1 30); do
  if docker exec "$G1_CONTAINER" pg_isready -h 127.0.0.1 -p 5432 -U postgres -d g1_test >/dev/null 2>&1; then
    G1_READY=1
    break
  fi
  sleep 1
done
[ "$G1_READY" = 1 ] || { docker logs "$G1_CONTAINER"; exit 1; }
export G1_TEST_DSN="postgresql://postgres:g1-local-disposable@127.0.0.1:$G1_PORT/g1_test"
"$G1_PYTHON" scripts/prepare_g1_database.py
sha256sum -c G0_SHA256SUMS.txt
"$G1_PYTHON" -m unittest discover -s tests -v
"$G1_PYTHON" -m unittest discover -s integration -v
