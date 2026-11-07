#!/bin/sh
# Local schema validation only. Downloads official tooling, never provisions.
set -eu
g3a_source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
g3a_validation_dir=$(mktemp -d /tmp/ltc-g3a-terraform.XXXXXXXX)
trap 'rm -rf -- "$g3a_validation_dir"' EXIT HUP INT TERM
python3 - "$g3a_source_dir" "$g3a_validation_dir" <<'PY'
from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from urllib.request import urlopen
import zipfile

source, work = map(Path, sys.argv[1:])
version = "1.17.1"
filename = f"terraform_{version}_linux_amd64.zip"
url = f"https://releases.hashicorp.com/terraform/{version}/{filename}"
checksums_url = f"https://releases.hashicorp.com/terraform/{version}/terraform_{version}_SHA256SUMS"
expected = "4638628058e71a55212b1c563712eac03b902128f40740f3dc8174df12135061"
# Verify both the pinned review and the current HTTPS official checksum listing.
with urlopen(checksums_url, timeout=30) as response:
    listing = response.read(1024 * 1024).decode("ascii")
entries = {line.split()[1].lstrip("*"): line.split()[0]
           for line in listing.splitlines() if len(line.split()) == 2}
if entries.get(filename) != expected:
    raise SystemExit("BLOCKED: reviewed tool checksum differs from official listing")
with urlopen(url, timeout=60) as response:
    archive = response.read(64 * 1024 * 1024)
if hashlib.sha256(archive).hexdigest() != expected:
    raise SystemExit("BLOCKED: Terraform archive checksum mismatch")
with zipfile.ZipFile(io.BytesIO(archive)) as package:
    executable = work / "terraform"
    executable.write_bytes(package.read("terraform"))
    executable.chmod(0o700)
configuration = work / "configuration"
configuration.mkdir()
for item in sorted(source.glob("*.tf.json")):
    shutil.copyfile(item, configuration / item.name)
shutil.copyfile(source / ".terraform.lock.hcl", configuration / ".terraform.lock.hcl")
for directory in ("data", "plugins"):
    (work / directory).mkdir()
(work / "terraformrc").write_text("disable_checkpoint = true\n")
# Deliberately omit inherited Google, Terraform-token and SDK credentials. Only
# transport proxy/CA settings and a basic executable path/locale are inherited.
allowed = {"PATH", "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
           "https_proxy", "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE",
           "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}
environment = {key: value for key, value in os.environ.items() if key in allowed}
environment.update(TF_CLI_CONFIG_FILE=str(work / "terraformrc"),
                   TF_DATA_DIR=str(work / "data"), TF_PLUGIN_CACHE_DIR=str(work / "plugins"),
                   TF_IN_AUTOMATION="1", TF_INPUT="0", CHECKPOINT_DISABLE="1",
                   TF_REGISTRY_CLIENT_TIMEOUT="15", TF_REGISTRY_DISCOVERY_RETRY="0",
                   GCE_METADATA_HOST="127.0.0.1:9",
                   GOOGLE_APPLICATION_CREDENTIALS=str(work / "NO_CREDENTIALS"))
print(f"Verified Terraform {version} official ZIP sha256={expected}", flush=True)
print("Isolated local validation; no backend, cloud credentials or Google API access.", flush=True)

def run(arguments, *, capture=False):
    print("$ terraform " + " ".join(arguments), flush=True)
    result = subprocess.run([str(executable), *arguments], cwd=configuration,
                            env=environment, text=True, stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None)
    if capture:
        print(result.stdout, flush=True)
    return result

identity = run(["version", "-json"], capture=True)
if identity.returncode or json.loads(identity.stdout)["terraform_version"] != version:
    raise SystemExit("BLOCKED: unexpected Terraform executable version")
formatted = run(["fmt", "-check", "-diff", "-no-color"])
if formatted.returncode:
    raise SystemExit(formatted.returncode)
# At most two initialization attempts. This only downloads the pinned provider;
# backend initialization is disabled and provider configuration is not applied.
for attempt in range(2):
    initialized = run(["init", "-backend=false", "-input=false", "-lockfile=readonly", "-no-color"])
    if initialized.returncode == 0:
        break
else:
    raise SystemExit("BLOCKED: provider initialization failed after two attempts")
validated = run(["validate", "-json", "-no-color"], capture=True)
if validated.returncode:
    raise SystemExit(validated.returncode)
report = json.loads(validated.stdout)
if not report.get("valid") or report.get("error_count"):
    raise SystemExit("BLOCKED: provider schema validation failed")
print("PASS: local Terraform/provider schema validation only; Google Cloud NOT_VALIDATED.", flush=True)
PY
