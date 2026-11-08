#!/usr/bin/env bash
# Complete historical regressions followed by the actual isolated browser proof.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${G3BR0_PYTHON:-python3}"
evidence="${G3BR0_EVIDENCE_DIR:?Choose a new absolute evidence directory outside the checkout}"
export_dir="${G3BR0_EXPORT_DIR:?Choose a new public evidence directory outside the private lab}"
# Historical scripts remain byte-identical and use bare docker. Force their
# transport to the verified local socket before any of them can create work.
unset DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH
export DOCKER_HOST=unix:///var/run/docker.sock
docker --host=unix:///var/run/docker.sock info >/dev/null
"$python_bin" - "$evidence" "$export_dir" "$PWD" <<'PY'
from pathlib import Path
import sys
private,public,checkout=map(Path,sys.argv[1:])
for target in (private,public):
    resolved=target.resolve()
    if not target.is_absolute() or target.exists() or resolved==checkout or checkout in resolved.parents:
        raise SystemExit('Choose new absolute evidence directories outside the checkout')
if private.resolve() in public.resolve().parents or public.resolve() in private.resolve().parents or private==public:
    raise SystemExit('Public and private evidence directories must be separate')
PY
[ ! -e "$evidence" ] || { echo 'Evidence directory must be new' >&2; exit 1; }
mkdir -m 700 -p "$evidence"
sha256sum -c G3BR0_BASELINE_SHA256SUMS.txt >"$evidence/baseline.txt"
npm ci --prefix browser_tests --ignore-scripts --omit=optional >"$evidence/npm-ci.log" 2>&1
# Avoid inherited fixed ports and recently-used rootless Docker publications.
read -r G3A_PORT G3A1_PORT < <("$python_bin" - <<'PY'
import secrets,socket
ports=[]
while len(ports)<2:
    candidate=12000+secrets.randbelow(12000)
    if candidate in ports: continue
    try:
        with socket.socket() as probe: probe.bind(('127.0.0.1',candidate))
    except OSError: continue
    ports.append(candidate)
print(*ports)
PY
)
export G3A_PORT G3A1_PORT
result=0
phase=historical
if G3A1_PYTHON="$python_bin" G3A1_EVIDENCE_DIR="$evidence/historical" \
    bash scripts/run_g3a1_local.sh >"$evidence/historical.log" 2>&1; then
    phase=local-browser
    G3BR0_PYTHON="$python_bin" bash scripts/run_g3br0_local.sh --test --output "$evidence/local" \
        >"$evidence/local-supervisor.log" 2>&1 || result=$?
else
    result=$?
fi
"$python_bin" scripts/collect_g3br0_evidence.py --lab "$evidence/local" --output "$export_dir" \
    --historical-log "$evidence/historical.log" --exit-code "$result" --phase "$phase"
cp "$evidence/baseline.txt" "$export_dir/baseline.txt"
exit "$result"
