#!/usr/bin/env bash
# Historical suites are executed unchanged in their own isolated database first.
set -euo pipefail
if [ -n "${PGSERVICE:-}" ] || [ -n "${PGHOSTADDR:-}" ]; then
 echo 'Refusing libpq service/host-address overrides in local laboratory' >&2
 exit 1
fi
cd "$(dirname "$0")/.."
G3A1_PYTHON=${G3A1_PYTHON:-python3}
G3A1_PORT=${G3A1_PORT:-55460}
G3A1_EVIDENCE_DIR=${G3A1_EVIDENCE_DIR:-/tmp/ltc-g3a1-evidence-$$}
mkdir -m 700 -p "$G3A1_EVIDENCE_DIR"
sha256sum -c G3A1_BASELINE_SHA256SUMS.txt
G3A_PYTHON="$G3A1_PYTHON" G3A_EVIDENCE_DIR="$G3A1_EVIDENCE_DIR/historical" \
 bash scripts/run_g3a_local.sh 2>&1 | tee "$G3A1_EVIDENCE_DIR/historical.txt"
G3A1_CONTAINER="ltc-g3a1-$$"
cleanup() { docker rm -f "$G3A1_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
docker run -d --name "$G3A1_CONTAINER" -p "127.0.0.1:$G3A1_PORT:5432" \
 -e POSTGRES_PASSWORD=g1-local-disposable -e POSTGRES_DB=g1_test \
 --tmpfs /var/lib/postgresql/data:rw,size=512m \
 postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9 \
 -c max_connections=180 >/dev/null
G3A1_READY=0
for attempt in $(seq 1 30); do
 if docker exec "$G3A1_CONTAINER" pg_isready -h 127.0.0.1 -p 5432 -U postgres -d g1_test >/dev/null 2>&1; then
  G3A1_READY=1
  break
 fi
 sleep 1
done
[ "$G3A1_READY" = 1 ] || { docker logs "$G3A1_CONTAINER"; exit 1; }
export G1_TEST_DSN="postgresql://postgres:g1-local-disposable@127.0.0.1:$G3A1_PORT/g1_test"
"$G3A1_PYTHON" scripts/prepare_g3a1_database.py
PYTHONPATH=integration:media_tests:cloud_tests "$G3A1_PYTHON" -m unittest discover -s upload_tests -v
PYTHONPATH=integration:media_tests:cloud_tests:upload_tests "$G3A1_PYTHON" scripts/benchmark_g3a1.py --output "$G3A1_EVIDENCE_DIR/shared-benchmark"
G3A1_PYTHON="$G3A1_PYTHON" bash scripts/run_g3a1_auth.sh 2>&1 | tee "$G3A1_EVIDENCE_DIR/shared-auth.txt"
printf 'G3A.1 local evidence: %s\n' "$G3A1_EVIDENCE_DIR"
