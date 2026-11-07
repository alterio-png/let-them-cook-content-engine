#!/usr/bin/env bash
# Local contracts + measured local load; never creates or calls cloud resources.
set -euo pipefail
cd "$(dirname "$0")/.."
G3A_PYTHON=${G3A_PYTHON:-python3}
G3A_PORT=${G3A_PORT:-55450}
G3A_CONTAINER="ltc-g3a-$$"
G3A_EVIDENCE_DIR=${G3A_EVIDENCE_DIR:-/tmp/ltc-g3a-evidence-$$}
mkdir -m 700 -p "$G3A_EVIDENCE_DIR"
cleanup() { docker rm -f "$G3A_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT HUP INT TERM
docker run -d --name "$G3A_CONTAINER" -p "127.0.0.1:$G3A_PORT:5432" \
 -e POSTGRES_PASSWORD=g1-local-disposable -e POSTGRES_DB=g1_test \
 --tmpfs /var/lib/postgresql/data:rw,size=512m \
 postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9 \
 -c max_connections=180 >/dev/null
G3A_READY=0
for attempt in $(seq 1 30); do
 if docker exec "$G3A_CONTAINER" pg_isready -h 127.0.0.1 -p 5432 -U postgres -d g1_test >/dev/null 2>&1; then
  G3A_READY=1
  break
 fi
 sleep 1
done
[ "$G3A_READY" = 1 ] || { docker logs "$G3A_CONTAINER"; exit 1; }
export G1_TEST_DSN="postgresql://postgres:g1-local-disposable@127.0.0.1:$G3A_PORT/g1_test"
sha256sum -c G0_SHA256SUMS.txt
sha256sum -c G2_BASELINE_SHA256SUMS.txt
sha256sum -c G3A_BASELINE_SHA256SUMS.txt
"$G3A_PYTHON" scripts/prepare_g3a_database.py
"$G3A_PYTHON" -m unittest discover -s tests -v
"$G3A_PYTHON" -m unittest discover -s integration -v
PYTHONPATH=integration "$G3A_PYTHON" -m unittest discover -s media_tests -v
PYTHONPATH=integration:media_tests "$G3A_PYTHON" -m unittest discover -s cloud_tests -v
PYTHONPATH=integration:media_tests "$G3A_PYTHON" scripts/benchmark_g3a.py \
 --output "$G3A_EVIDENCE_DIR/benchmark" --jobs 20,50 --workers 2 --duration-ms 300
G3A_PYTHON="$G3A_PYTHON" bash scripts/run_local_auth.sh 2>&1 | tee "$G3A_EVIDENCE_DIR/auth.txt"
printf 'G3A local evidence: %s\n' "$G3A_EVIDENCE_DIR"
