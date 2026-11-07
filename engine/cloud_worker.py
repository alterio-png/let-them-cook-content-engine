"""Authenticated short task callback and immutable Cloud Run worker entrypoint.

The callback is a framework-neutral handler, not a deployed HTTP service. Google
service identity uses a separate RS256 trust boundary from Supabase end users.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import threading
import time
from types import MappingProxyType
from contextlib import contextmanager

import jwt

from .cloud_auth import AuthError, TrustedKey
from .control_plane import StaleLease
from .media_control import MediaControlPlane
from .media_runtime import Limits
from .cloud_run import LaunchBoundMediaControl, LaunchCapacity
from .cloud_tasks import canonical_uuid
from .media_pipeline import MediaWorker


class GoogleServiceIdentityVerifier:
    """Configured public Google signing keys; no token-controlled discovery URL.

    G3A tests use local RSA keys only. Actual Google-issued identity, key rotation,
    Cloud Run ingress/IAM and Cloud Tasks actAs remain NOT_VALIDATED until G3B.
    """
    def __init__(self, *, audience, service_account_email, service_account_subject, keys):
        if (not isinstance(audience, str) or not audience.startswith("https://")
                or not re.fullmatch(r"[0-9]{10,30}", service_account_subject)
                or not service_account_email.endswith(".iam.gserviceaccount.com") or not keys
                or any(not isinstance(k, TrustedKey) or k.algorithm != "RS256" for k in keys.values())):
            raise ValueError("Explicit Google audience, service identity and RSA public keys required")
        self.audience, self.email, self.subject = audience, service_account_email, service_account_subject
        self.keys = MappingProxyType(dict(keys))

    def verify(self, token):
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise ValueError
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "RS256" or header.get("typ", "JWT") != "JWT"
                    or header.get("crit") or any(key in header for key in ("jku", "jwk", "x5u", "x5c", "zip"))):
                raise ValueError
            trusted = self.keys[header["kid"]]
            claims = jwt.decode(token, trusted.key, algorithms=["RS256"], audience=self.audience,
                                options={"require": ["iss", "sub", "aud", "exp", "iat", "email", "email_verified"],
                                         "strict_aud": True})
            if (claims["iss"] not in {"https://accounts.google.com", "accounts.google.com"}
                    or claims["sub"] != self.subject or claims["email"] != self.email
                    or claims["email_verified"] is not True
                    or any(type(claims[key]) is not int for key in ("exp", "iat"))
                    or not 0 < claims["exp"] - claims["iat"] <= 3600):
                raise ValueError
            return self.email
        except Exception:
            raise AuthError("Invalid task service credential") from None


class TaskSignalHandler:
    def __init__(self, controller, *, verifier):
        # No default verifier, debug header, end-user-token fallback or bypass.
        if not isinstance(verifier, GoogleServiceIdentityVerifier):
            raise ValueError("A separate cryptographic Google service verifier is required")
        self.controller, self.verifier = controller, verifier

    def handle(self, authorization, body):
        try:
            if (not isinstance(authorization, str) or not authorization.startswith("Bearer ")
                    or authorization.count(" ") != 1):
                raise AuthError("Invalid task service credential")
            self.verifier.verify(authorization[7:])
        except AuthError:
            return 401, {"error": "unauthorized"}
        try:
            if not isinstance(body, bytes) or len(body) > 2048:
                raise ValueError
            signal = json.loads(body)
            if (not isinstance(signal, dict) or set(signal) != {"version", "event_id", "job_id", "organization_id"}
                    or type(signal["version"]) is not int or signal["version"] != 1):
                raise ValueError
            for key in ("event_id", "job_id", "organization_id"):
                canonical_uuid(signal[key])
        except (ValueError, TypeError, KeyError):
            return 400, {"error": "invalid_signal"}
        try:
            self.controller.launch(signal["job_id"], signal["organization_id"], signal["event_id"])
        except ValueError:
            return 400, {"error": "invalid_signal"}
        except LaunchCapacity:
            return 503, {"error": "capacity_reserved"}
        except Exception:
            return 503, {"error": "temporarily_unavailable"}
        return 202, {"state": "recorded"}


class _LeaseKeeper:
    """Independent renewal during synchronous object I/O, bounded by job time."""
    def __init__(self, dsn, lease_seconds, timeout_seconds):
        self.control = MediaControlPlane(dsn)
        self.lease_seconds, self.timeout_seconds = lease_seconds, timeout_seconds
        self.stopped, self.thread, self.error = threading.Event(), None, None

    def start(self, claim):
        deadline = time.monotonic() + self.timeout_seconds
        interval = max(0.1, self.lease_seconds / 3)
        def keep():
            while not self.stopped.wait(min(interval, max(0, deadline - time.monotonic()))):
                if time.monotonic() >= deadline:
                    self.error = StaleLease("G3 whole-job lease keeper deadline reached")
                    return
                try:
                    self.control.renew_lease(claim["id"], claim["lease_token"], claim["fence"],
                                             self.lease_seconds)
                except Exception:
                    try:
                        completed = self.control.get_job(claim["id"])
                        if (completed is not None and completed["state"] == "completed"
                                and completed["fence"] == claim["fence"]):
                            self.stopped.set()
                            return
                    except Exception:
                        pass
                    self.error = StaleLease("G3 independent lease renewal failed")
                    return
        self.thread = threading.Thread(target=keep, name="g3a-lease-keeper", daemon=True)
        self.thread.start()

    def check(self):
        if self.error is not None:
            raise self.error

    def stop(self):
        self.stopped.set()
        if self.thread is not None:
            # G1 has connect/statement/lock timeouts. Stop future renewals and
            # bound this join; a network-blackholed in-flight libpq call still
            # requires runtime termination/network timeout validation in G3B.
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise StaleLease("G3 lease keeper did not stop within its SQL bound")


class _GuardedLaunchControl(LaunchBoundMediaControl):
    def __init__(self, dsn, launch, keeper):
        super().__init__(dsn, launch)
        self.keeper = keeper

    @contextmanager
    def transaction(self):
        self.keeper.check()
        with super().transaction() as db:
            yield db

    def finish_job(self, job_id, lease_token, fence, manifest):
        result = super().finish_job(job_id, lease_token, fence, manifest)
        # The committed authoritative outcome ends renewal before G2's
        # after_commit hook/metrics. A concurrent renewal may observe completion;
        # it must not turn an already valid result into a false stale failure.
        self.keeper.stop()
        self.keeper.error = None
        return result

    def claim_job(self, job_id, lease_seconds=30):
        claim = super().claim_job(job_id, lease_seconds)
        if claim is not None:
            self.keeper.start(claim)  # The unchanged G1 claim has committed.
        return claim


def run_bound_worker(dsn, store, scratch_root, launch, *, lease_seconds=30, limits=None,
                     fault_hook=None):
    limits = limits or Limits()
    keeper = _LeaseKeeper(dsn, lease_seconds, limits.timeout_seconds)
    worker = MediaWorker(dsn, store, scratch_root, limits=limits,
                         lease_seconds=lease_seconds, fault_hook=fault_hook)
    worker.control = _GuardedLaunchControl(dsn, launch, keeper)
    try:
        return worker.run(canonical_uuid(launch["job_id"]))
    finally:
        keeper.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="One immutable G3A media launch")
    for flag in ("launch-id", "organization-id", "job-id", "event-id"):
        parser.add_argument("--" + flag, required=True, type=canonical_uuid)
    parser.add_argument("--expected-fence", required=True, type=int)
    args = parser.parse_args(argv)
    if args.expected_fence < 1:
        parser.error("expected fence must be positive")
    launch = {"id": args.launch_id, "organization_id": args.organization_id,
              "job_id": args.job_id, "event_id": args.event_id, "expected_fence": args.expected_fence}
    store_type = os.environ.get("G3_OBJECT_STORE", "local")
    if store_type == "local":
        from .object_store import LocalObjectStore
        store = LocalObjectStore(Path(os.environ["G3_LOCAL_STORE_ROOT"]))
    elif store_type == "gcs":
        # Explicit cloud runtime only. No metadata request is made by local tests.
        from .cloud_transport import UrllibCloudTransport
        from .gcs_store import GCSObjectStore
        store = GCSObjectStore(os.environ["G3_GCS_BUCKET"], transport=UrllibCloudTransport(
            MetadataAccessToken(), allowed_hosts=("storage.googleapis.com",)))
    else:
        parser.error("unsupported object store")
    row = run_bound_worker(os.environ["G3_WORKER_DSN"], store,
                           Path(os.environ["G3_SCRATCH_ROOT"]), launch)
    print(json.dumps({"job_id": args.job_id, "state": None if row is None else row["state"]}))
    return 0 if row is not None and row["state"] == "completed" else 1


class MetadataAccessToken:
    """Fixed Google metadata ADC endpoint; future runtime contract, NOT_VALIDATED.

    Never accepts a URL or caller token. Network use occurs only when explicitly
    starting the GCS worker, never at import or during local contract tests.
    """
    def __call__(self):
        from urllib.request import Request, ProxyHandler, build_opener
        from .cloud_transport import _NoRedirect, CloudTransportError
        request = Request("http://metadata.google.internal/computeMetadata/v1/instance/"
                          "service-accounts/default/token", headers={"Metadata-Flavor": "Google"})
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=3) as response:
                if response.headers.get("Metadata-Flavor") != "Google":
                    raise ValueError
                body = response.read(16385)
                if len(body) > 16384:
                    raise ValueError
                value = json.loads(body)
                token = value["access_token"]
                if (value.get("token_type") != "Bearer" or not isinstance(token, str)
                        or not token or any(c.isspace() for c in token)):
                    raise ValueError
                return token
        except Exception:
            raise CloudTransportError("Runtime access token is unavailable") from None


if __name__ == "__main__":
    raise SystemExit(main())
