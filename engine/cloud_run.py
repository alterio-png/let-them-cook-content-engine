"""Cloud Run Jobs REST launch contracts backed by a durable PostgreSQL intent.

jobs.run is not idempotent. Each immutable intent permits exactly one controller
call; uncertainty reserves its slot until reconciliation. This bounds launches,
not provider billing or exactly-once physical execution.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import re
import threading
from uuid import uuid4

from .cloud_tasks import canonical_uuid, provider_component
from .cloud_transport import CloudTransportError
from .control_plane import StaleLease, _audit
from .media_control import MediaControlPlane


class LaunchUncertain(RuntimeError):
    """Provider acceptance is unknown; do not blindly repeat jobs.run."""


class LaunchCapacity(RuntimeError):
    pass


class CloudRunJobsAdapter:
    def __init__(self, project, location, job, *, transport, container="worker", timeout=10):
        self.job_name = "/".join(("projects", provider_component(project), "locations",
                                  provider_component(location), "jobs", provider_component(job)))
        self.container = provider_component(container)
        if not 0 < timeout <= 30:
            raise ValueError("Invalid API timeout")
        self.transport, self.timeout = transport, timeout

    @staticmethod
    def execution_args(launch):
        args = []
        for flag, key in (("launch-id", "id"), ("organization-id", "organization_id"),
                          ("job-id", "job_id"), ("event-id", "event_id")):
            args.extend(("--" + flag, canonical_uuid(launch[key])))
        fence = launch["expected_fence"]
        if type(fence) is not int or fence < 1:
            raise ValueError("Invalid immutable fence")
        return args + ["--expected-fence", str(fence)]

    def _operation(self, data):
        name = data.get("name")
        prefix = self.job_name.rsplit("/jobs/", 1)[0] + "/operations/"
        if (not isinstance(name, str) or not name.startswith(prefix)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", name[len(prefix):])):
            raise LaunchUncertain("Provider operation identity is invalid")
        return name

    def run_launch(self, launch):
        body = {"overrides": {"taskCount": 1, "timeout": "300s", "containerOverrides": [
            {"name": self.container, "args": self.execution_args(launch)}]}}
        try:
            response = self.transport.request(
                "POST", "https://run.googleapis.com/v2/" + self.job_name + ":run",
                headers={"Content-Type": "application/json"},
                body=json.dumps(body, separators=(",", ":")).encode(), timeout=self.timeout,
                max_response_bytes=65536)
            if response.status not in {200, 201, 202}:
                raise LaunchUncertain("jobs.run acceptance was not confirmed")
            return self._operation(json.loads(response.body))
        except LaunchUncertain:
            raise
        except Exception:
            raise LaunchUncertain("jobs.run response is uncertain") from None

    def get_operation(self, name):
        # Validate before forwarding any authenticated request.
        self._operation({"name": name})
        response = self.transport.request("GET", "https://run.googleapis.com/v2/" + name,
                                          headers={}, body=b"", timeout=self.timeout,
                                          max_response_bytes=65536)
        if response.status != 200:
            raise LaunchUncertain("Stored operation is not confirmed")
        try:
            result = json.loads(response.body)
            self._operation(result)
            if result["name"] != name or type(result.get("done", False)) is not bool:
                raise ValueError
            return result
        except (TypeError, ValueError, KeyError):
            raise LaunchUncertain("Invalid operation response") from None


    def get_execution(self, name):
        prefix = self.job_name + "/executions/"
        if (not isinstance(name, str) or not name.startswith(prefix)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", name[len(prefix):])):
            raise LaunchUncertain("Execution identity is invalid")
        response = self.transport.request("GET", "https://run.googleapis.com/v2/" + name,
                                          headers={}, body=b"", timeout=self.timeout,
                                          max_response_bytes=65536)
        if response.status != 200:
            raise LaunchUncertain("Execution stop is not confirmed")
        try:
            result = json.loads(response.body)
            if result["name"] != name:
                raise ValueError
            counts = [result.get(key, 0) for key in
                      ("taskCount", "runningCount", "succeededCount", "failedCount", "cancelledCount")]
            if any(type(count) is not int or count < 0 for count in counts):
                raise ValueError
            finished = (isinstance(result.get("completionTime"), str)
                        and bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z", result["completionTime"]))
                        and counts[0] >= 1 and counts[1] == 0 and sum(counts[2:]) == counts[0])
            return {"name": name, "finished": finished}
        except (ValueError, KeyError, TypeError):
            raise LaunchUncertain("Invalid execution status") from None


class CloudLaunchControl(MediaControlPlane):
    def prepare_launch(self, job_id, organization_id, event_id, *, max_inflight=2):
        job_id, organization_id, event_id = map(canonical_uuid, (job_id, organization_id, event_id))
        if type(max_inflight) is not int or not 1 <= max_inflight <= 32:
            raise ValueError("Global launch cap must be 1..32")
        with self.transaction() as db:
            # All producers serialize the global physical-launch slot reservation.
            db.execute("SELECT pg_advisory_xact_lock(744379331)")
            self._lock_organization(db, job_id)
            job = db.execute("""SELECT *,available_at<=clock_timestamp() AS ready,
                          lease_expires_at>clock_timestamp() AS live
                          FROM public.render_jobs WHERE id=%s FOR UPDATE""", (job_id,)).fetchone()
            if job is None or str(job["organization_id"]) != organization_id:
                raise ValueError("Job identity does not match organization")
            event = db.execute("""SELECT id FROM public.render_outbox WHERE id=%s
                         AND job_id=%s AND organization_id=%s""",
                               (event_id, job_id, organization_id)).fetchone()
            spec = db.execute("SELECT job_id FROM public.media_job_specs WHERE job_id=%s", (job_id,)).fetchone()
            if event is None or spec is None:
                raise ValueError("Signal is not a durable media event")
            if job["state"] not in {"queued", "running"} or not job["ready"]:
                return None
            if job["state"] == "running" and job["live"]:
                return None
            if job["attempt_count"] >= job["max_attempts"]:
                return None  # The ordinary G2 sweep records the terminal G1 transition.
            fence = job["fence"] + 1
            previous = db.execute("""SELECT * FROM public.cloud_job_launches
                                   WHERE job_id=%s AND expected_fence=%s""", (job_id, fence)).fetchone()
            if previous is not None:
                return {**previous, "new_intent": False}
            unresolved = db.execute("""SELECT organization_id,count(*) AS n
                        FROM public.cloud_job_launches WHERE state <> 'closed'
                        GROUP BY organization_id""").fetchall()
            active = db.execute("""SELECT id,organization_id FROM public.render_jobs
                         WHERE state='running' AND lease_expires_at>clock_timestamp()""").fetchall()
            # Count the union so a claimed launch's PG worker does not count twice.
            pending = db.execute("SELECT job_id FROM public.cloud_job_launches WHERE state<>'closed'").fetchall()
            reserved_ids = {str(row["job_id"]) for row in pending}
            extra_active = [row for row in active if str(row["id"]) not in reserved_ids]
            total = sum(row["n"] for row in unresolved) + len(extra_active)
            org_total = sum(row["n"] for row in unresolved if str(row["organization_id"]) == organization_id)
            org_total += sum(str(row["organization_id"]) == organization_id for row in extra_active)
            quota = db.execute("SELECT max_active_jobs FROM public.org_quotas WHERE organization_id=%s",
                               (organization_id,)).fetchone()
            if total >= max_inflight or org_total >= quota["max_active_jobs"]:
                raise LaunchCapacity("Durable global or organization launch capacity is reserved")
            row = db.execute("""INSERT INTO public.cloud_job_launches
                   (id,organization_id,job_id,event_id,expected_fence,state)
                   VALUES (%s,%s,%s,%s,%s,'submitting') RETURNING *""",
                             (uuid4(), organization_id, job_id, event_id, fence)).fetchone()
            _audit(db, row, "cloud_launch_intent", {"launch_id": str(row["id"]), "fence": fence})
            return {**row, "new_intent": True}

    def record_response(self, launch_id, operation_name=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM public.cloud_job_launches WHERE id=%s FOR UPDATE",
                             (canonical_uuid(launch_id),)).fetchone()
            if row is None:
                raise ValueError("Unknown launch")
            state = "accepted" if operation_name else "uncertain"
            # A fast worker can claim before the jobs.run response arrives.
            if row["state"] in {"claimed", "closed"}:
                state = row["state"]
            row = db.execute("""UPDATE public.cloud_job_launches SET state=%s,
                      operation_name=coalesce(operation_name,%s),last_error=%s,
                      touched_at=clock_timestamp() WHERE id=%s RETURNING *""",
                             (state, operation_name, None if operation_name else "response_uncertain", launch_id)).fetchone()
            _audit(db, row, "cloud_launch_" + ("accepted" if operation_name else "uncertain"),
                   {"launch_id": str(launch_id)})
            return row

    def get_launch(self, launch_id):
        with self.transaction() as db:
            return db.execute("SELECT * FROM public.cloud_job_launches WHERE id=%s",
                              (canonical_uuid(launch_id),)).fetchone()

    def retire_stopped_launch(self, launch_id):
        """Controller-only, after authenticated provider terminal evidence.

        An unstarted launch consumes a bounded G1 failure attempt so the next
        intent has a new fence. A stopped claimed execution may release its slot;
        the next claim still waits for its G1 lease to expire. No jobs.run here.
        """
        row = self.get_launch(launch_id)
        if row is None or row["state"] == "closed":
            return row
        if row["state"] != "claimed":
            bound = LaunchBoundMediaControl(self.dsn, row)
            try:
                claim = bound.claim_job(row["job_id"])
                if claim is not None:
                    bound.fail_job(claim["id"], claim["lease_token"], claim["fence"], "crash")
                else:
                    current = self.get_job(row["job_id"])
                    if (current["state"] not in {"completed", "failed"}
                            and current["fence"] < row["expected_fence"]):
                        return row  # Retry retirement after quota/backoff permits a G1 transition.
            except StaleLease:
                pass  # A separate durable sweep may already own a newer fence.
        with self.transaction() as db:
            row = db.execute("""UPDATE public.cloud_job_launches SET state='closed',
                     last_error='provider_execution_stopped',touched_at=clock_timestamp()
                     WHERE id=%s RETURNING *""", (canonical_uuid(launch_id),)).fetchone()
            _audit(db, row, "cloud_launch_stopped", {"launch_id": str(launch_id)})
            return row


class LaunchController:
    def __init__(self, dsn, adapter, *, max_inflight=2):
        self.control, self.adapter, self.max_inflight = CloudLaunchControl(dsn), adapter, max_inflight

    def launch(self, job_id, organization_id, event_id):
        intent = self.control.prepare_launch(job_id, organization_id, event_id,
                                             max_inflight=self.max_inflight)
        if intent is None or not intent["new_intent"]:
            return intent
        try:
            operation = self.adapter.run_launch(intent)
        except Exception:
            self.control.record_response(intent["id"])
            return self.control.get_launch(intent["id"])
        return self.control.record_response(intent["id"], operation)

    def reconcile(self, launch_id):
        row = self.control.get_launch(launch_id)
        if row is None or row["state"] == "closed" or row["operation_name"] is None:
            return row  # An absent name/history never proves no physical execution.
        operation = self.adapter.get_operation(row["operation_name"])
        if operation.get("done") is True and isinstance(operation.get("response"), dict):
            execution = self.adapter.get_execution(operation["response"].get("name"))
            if execution["finished"]:
                self.control.retire_stopped_launch(row["id"])
        # An operation error does not by itself prove no container is running.
        # Unknown acceptance/history requires separate operator/provider evidence.
        return self.control.get_launch(launch_id)


class LaunchBoundMediaControl(MediaControlPlane):
    """Bind the unchanged G1 claim to one immutable launch, in one transaction."""
    def __init__(self, dsn, launch):
        super().__init__(dsn)
        self.launch = dict(launch)
        self._local = threading.local()

    @contextmanager
    def transaction(self):
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            yield existing
        else:
            with super().transaction() as db:
                yield db

    def claim_job(self, job_id, lease_seconds=30):
        expected = self.launch
        if canonical_uuid(job_id) != canonical_uuid(expected["job_id"]):
            raise StaleLease("Launch cannot claim a different job")
        with super().transaction() as db:
            self._lock_organization(db, job_id)
            job = db.execute("SELECT * FROM public.render_jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
            intent = db.execute("SELECT * FROM public.cloud_job_launches WHERE id=%s FOR UPDATE",
                                (canonical_uuid(expected["id"]),)).fetchone()
            if (job is None or intent is None or intent["state"] not in {"submitting", "accepted", "uncertain"}
                    or any(str(intent[key]) != str(expected[key]) for key in
                           ("id", "job_id", "organization_id", "event_id", "expected_fence"))
                    or str(job["organization_id"]) != str(expected["organization_id"])
                    or job["fence"] + 1 != expected["expected_fence"]):
                raise StaleLease("Launch intent is stale, consumed, or mismatched")
            self._local.connection = db
            try:
                claim = super().claim_job(job_id, lease_seconds)
            finally:
                self._local.connection = None
            if claim is None:
                return None
            if claim["state"] != "running" or claim["fence"] != intent["expected_fence"]:
                raise StaleLease("Launch did not obtain its immutable fence")
            db.execute("""UPDATE public.cloud_job_launches SET state='claimed',claimed_at=clock_timestamp(),
                         touched_at=clock_timestamp() WHERE id=%s""", (intent["id"],))
            _audit(db, intent, "cloud_launch_claimed", {"launch_id": str(intent["id"]), "fence": claim["fence"]})
            return claim
