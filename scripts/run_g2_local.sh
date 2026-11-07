#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
G2_PYTHON=${G2_PYTHON:-python3}
G2_PORT=${G2_PORT:-55438}
G2_CONTAINER="ltc-g2-$$"
cleanup() { docker rm -f "$G2_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
docker run -d --name "$G2_CONTAINER" -p "127.0.0.1:$G2_PORT:5432" \
 -e POSTGRES_PASSWORD=g1-local-disposable -e POSTGRES_DB=g1_test \
 --tmpfs /var/lib/postgresql/data:rw,size=512m \
 postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9 \
 -c max_connections=160 >/dev/null
G2_READY=0
for attempt in $(seq 1 30); do
 if docker exec "$G2_CONTAINER" pg_isready -h 127.0.0.1 -p 5432 -U postgres -d g1_test >/dev/null 2>&1; then
  G2_READY=1
  break
 fi
 sleep 1
done
[ "$G2_READY" = 1 ] || { docker logs "$G2_CONTAINER"; exit 1; }
export G1_TEST_DSN="postgresql://postgres:g1-local-disposable@127.0.0.1:$G2_PORT/g1_test"
sha256sum -c G0_SHA256SUMS.txt
sha256sum -c G2_BASELINE_SHA256SUMS.txt
"$G2_PYTHON" scripts/prepare_g2_database.py
"$G2_PYTHON" -m unittest discover -s tests -v
"$G2_PYTHON" -m unittest discover -s integration -v
PYTHONPATH=integration "$G2_PYTHON" -m unittest discover -s media_tests -v
if [ -n "${G2_EVIDENCE_DIR:-}" ]; then
 "$G2_PYTHON" -m engine.media_cli --output "$G2_EVIDENCE_DIR"
fi
