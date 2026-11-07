"""Cloud Tasks delivers short wake signals; PostgreSQL remains authoritative.

Provider name retention is finite. This adapter deliberately has no authoritative
lookup() API and must not be passed to the unchanged G1 Dispatcher.
"""
from __future__ import annotations

import base64
import json
import re
from uuid import UUID
from urllib.parse import urlsplit

from .cloud_transport import CloudTransportError
from .handoff import Dispatcher, QueueRateLimited


def canonical_uuid(value):
    text = str(value)
    if str(UUID(text)) != text:
        raise ValueError("Expected canonical UUID")
    return text


def provider_component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("Invalid configured provider resource")
    return value


class CloudTasksAdapter:
    def __init__(self, project, location, queue, *, callback_url,
                 service_account_email, audience, transport, timeout=10):
        self.parent = "/".join(("projects", provider_component(project), "locations",
                                provider_component(location), "queues", provider_component(queue)))
        parsed = urlsplit(callback_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.query or parsed.port not in {None, 443}
                or any(c.isspace() for c in callback_url)):
            raise ValueError("A fixed HTTPS callback is required")
        if (not isinstance(service_account_email, str) or not re.fullmatch(
                r"[a-z][a-z0-9-]{4,62}@[a-z][a-z0-9-]{4,62}\.iam\.gserviceaccount\.com",
                service_account_email)):
            raise ValueError("A dedicated configured service account is required")
        if audience != callback_url or not 0 < timeout <= 30:
            raise ValueError("Audience must exactly match the fixed callback; timeout <=30s")
        self.callback_url, self.service_account_email = callback_url, service_account_email
        self.audience, self.transport, self.timeout = audience, transport, timeout

    def ensure_signal(self, event):
        event_id, job_id, org = (canonical_uuid(event[key]) for key in
                                 ("id", "job_id", "organization_id"))
        name = self.parent + "/tasks/" + event_id
        signal = {"version": 1, "event_id": event_id, "job_id": job_id, "organization_id": org}
        task = {"name": name, "dispatchDeadline": "30s", "httpRequest": {
            "httpMethod": "POST", "url": self.callback_url,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(json.dumps(signal, sort_keys=True,
                                                separators=(",", ":")).encode()).decode(),
            "oidcToken": {"serviceAccountEmail": self.service_account_email,
                          "audience": self.audience}}}
        response = self.transport.request(
            "POST", "https://cloudtasks.googleapis.com/v2/" + self.parent + "/tasks",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"task": task}, separators=(",", ":")).encode(), timeout=self.timeout,
            max_response_bytes=65536)
        if response.status == 429:
            raise QueueRateLimited("Cloud Tasks rate limited")
        if response.status == 409:
            # This is only a wake signal. The durable event/job binding and worker
            # claim are checked in PostgreSQL even if names expire at the provider.
            return {"name": name, "duplicate": True}
        if response.status not in {200, 201}:
            raise CloudTransportError("Cloud Tasks signal was not confirmed")
        try:
            result = json.loads(response.body)
            if result["name"] != name:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise CloudTransportError("Cloud Tasks response identity is invalid") from None
        return {"name": name, "duplicate": False}


class CloudDispatcher(Dispatcher):
    """G3 dispatcher: finite provider deduplication, durable PG delivery state.

    A timeout may have created a task. Retrying the same name is safe within the
    provider window, and after expiry may create another signal. Signals cannot
    submit a logical job. PostgreSQL leases/fences govern all render claims and
    the separate database sweep recovers work even after provider history loss.
    """
    def dispatch_one(self, queue):
        event = self.claim_outbox()
        if event is None:
            return None
        try:
            queue.ensure_signal(event)
        except QueueRateLimited:
            updated = self.release(event, "429")
        except (TimeoutError, CloudTransportError):
            updated = self.release(event, "timeout")
        except Exception:
            updated = self.release(event, "unknown")
        else:
            self.ack(event)
            return {"state": "dispatched", "event_id": str(event["id"])}
        return {"state": "reconcile" if updated["state"] == "reconcile" else "retry",
                "event_id": str(event["id"])}
