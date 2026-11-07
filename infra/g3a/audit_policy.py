"""Offline policy audit of REVIEW-ONLY Terraform JSON, without cloud access.

This checks security properties of the submitted topology. It is not Terraform
syntax/schema validation and it never runs provider binaries or remote APIs.
"""
from __future__ import annotations

import json
from pathlib import Path


class PolicyViolation(ValueError):
    pass


def load_configuration(directory: Path) -> dict:
    if any(directory.glob("*.tf")):
        raise PolicyViolation("HCL sidecars bypass the audited JSON contract")
    merged: dict = {}
    for path in sorted(directory.glob("*.tf.json")):
        document = json.loads(path.read_text())
        for section, value in document.items():
            target = merged.setdefault(section, {})
            if section == "resource":
                for kind, instances in value.items():
                    names = target.setdefault(kind, {})
                    if names.keys() & instances.keys():
                        raise PolicyViolation("duplicate resource address")
                    names.update(instances)
            else:
                if target.keys() & value.keys():
                    raise PolicyViolation("duplicate Terraform declaration")
                target.update(value)
    return merged


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyViolation(message)


def audit(config: dict) -> None:
    resource = config["resource"]
    variables = config["variable"]
    require(set(config["provider"]["google"]) <= {"project", "region"},
            "provider may not contain credentials, keys or custom endpoints")
    require("backend" not in config["terraform"], "no remote backend is authorized in G3A")
    require(config["terraform"]["required_providers"]["google"] ==
            {"source": "hashicorp/google", "version": "= 8.10.0"},
            "reviewed official provider exact pin must be retained")
    for name, definition in variables.items():
        for validation in definition.get("validation", []):
            condition = validation.get("condition", "")
            require(condition.startswith("${") and condition.endswith("}") and
                    ("var." + name) in condition,
                    "JSON variable validation must contain an interpolated expression")
    require(variables["staging_authorized"].get("default") is False,
            "provisioning authorization must default false")
    require(variables["cloud_runtime_images_approved"].get("default") is False,
            "unvalidated runtime images must remain blocked")
    guard = resource["terraform_data"]["staging_authorization"]
    conditions = {x["condition"] for x in guard["lifecycle"]["precondition"]}
    require("${var.staging_authorized}" in conditions and
            "${var.cloud_runtime_images_approved}" in conditions,
            "authorization and runtime gates must both be enforced")
    allowed = {
        "terraform_data", "google_project_service", "google_service_account", "google_project_iam_member",
        "google_storage_bucket", "google_project_iam_custom_role",
        "google_storage_bucket_iam_member", "google_cloud_tasks_queue",
        "google_cloud_tasks_queue_iam_member", "google_service_account_iam_member",
        "google_cloud_run_v2_job", "google_cloud_run_v2_service",
        "google_cloud_run_v2_service_iam_member", "google_cloud_run_v2_job_iam_member",
        "google_secret_manager_secret_iam_member", "google_billing_budget",
    }
    require(set(resource) <= allowed, "unexpected resource kind or unscoped IAM")
    flattened = [(kind, name, obj) for kind, objects in resource.items()
                 for name, obj in objects.items()]
    encoded = json.dumps(config)
    require("allUsers" not in encoded and "allAuthenticatedUsers" not in encoded,
            "public principal forbidden")
    require("roles/owner" not in encoded and "roles/editor" not in encoded,
            "primitive broad role forbidden")
    for kind, _, obj in flattened:
        if kind != "terraform_data":
            require("terraform_data.staging_authorization" in obj.get("depends_on", []),
                    "every resource must depend on authorization gate")
        if "iam_member" in kind:
            require(obj.get("member", "").startswith("serviceAccount:"),
                    "only dedicated workload service accounts may receive runtime roles")
        if kind == "google_project_service":
            require(obj.get("disable_on_destroy") is False,
                    "do not implicitly disable shared APIs")
    project_bindings = resource.get("google_project_iam_member", {})
    require(set(project_bindings) == {"orchestrator_operation_observer"},
            "only minimal project-scoped operation observation IAM is permitted")
    operation_binding = project_bindings["orchestrator_operation_observer"]
    require(operation_binding.get("project") == "${var.project_id}" and
            operation_binding.get("role") == "${google_project_iam_custom_role.operation_observer.name}" and
            operation_binding.get("member") == "serviceAccount:${google_service_account.orchestrator.email}",
            "project observation must be bound only to this staging controller")
    iam_roles = {
        "google_project_iam_member": {"${google_project_iam_custom_role.operation_observer.name}"},
        "google_storage_bucket_iam_member": {
            "${google_project_iam_custom_role.api_media.name}",
            "${google_project_iam_custom_role.worker_media.name}"},
        "google_cloud_tasks_queue_iam_member": {"roles/cloudtasks.enqueuer"},
        "google_service_account_iam_member": {
            "roles/iam.serviceAccountUser"},
        "google_cloud_run_v2_service_iam_member": {"roles/run.invoker"},
        "google_cloud_run_v2_job_iam_member": {"roles/run.jobsExecutorWithOverrides",
            "${google_project_iam_custom_role.execution_observer.name}"},
        "google_secret_manager_secret_iam_member": {"roles/secretmanager.secretAccessor"},
    }
    for kind, _, obj in flattened:
        if kind in iam_roles:
            require(obj.get("role") in iam_roles[kind], "runtime IAM role exceeds least privilege")
        if kind == "google_service_account_iam_member":
            require(obj.get("service_account_id") == "${google_service_account.task_caller.name}",
                    "token minting/actAs must be scoped to task caller only")
            require(obj["member"] in {
                "serviceAccount:${google_service_account.dispatcher.email}",
                "serviceAccount:service-${var.project_number}@gcp-sa-cloudtasks.iam.gserviceaccount.com"},
                "only dispatcher or the Google-managed task agent can attach the task caller")
    bucket = resource["google_storage_bucket"]["media"]
    require(bucket.get("uniform_bucket_level_access") is True and
            bucket.get("public_access_prevention") == "enforced", "bucket must be private and uniform")
    require(bucket.get("force_destroy") is False and
            bucket.get("lifecycle", {}).get("prevent_destroy") is True,
            "media destruction must fail closed")
    require(bucket.get("versioning") == [{"enabled": True}], "object versioning required")
    for rule in bucket.get("lifecycle_rule", []):
        require(rule["action"] == [{"type": "AbortIncompleteMultipartUpload"}],
                "no blind age deletion of authoritative assets or manifests")
        require(rule["condition"][0].get("age", 0) >= 1, "incomplete upload expiry must be bounded")
    roles = resource["google_project_iam_custom_role"]
    require(set(roles["api_media"]["permissions"]) <= {"storage.objects.create", "storage.objects.get"},
            "API may not list/delete/update arbitrary media")
    require(set(roles["worker_media"]["permissions"]) ==
            {"storage.objects.create", "storage.objects.get", "storage.objects.list", "storage.objects.delete"},
            "worker object role must exclude overwrite/update and IAM changes")
    queue = resource["google_cloud_tasks_queue"]["media"]
    rate, retry = queue["rate_limits"][0], queue["retry_config"][0]
    require(0 < rate["max_dispatches_per_second"] <= 2 and
            0 < rate["max_concurrent_dispatches"] <= 2, "queue signal rate/concurrency unbounded")
    require(1 <= retry["max_attempts"] <= 5 and retry["max_backoff"] == "60s" and
            retry["max_retry_duration"] == "300s", "provider retries must be bounded")
    job = resource["google_cloud_run_v2_job"]["worker"]["template"][0]
    require(job["task_count"] == 1 and job["parallelism"] == 1,
            "one physical task per admitted launch")
    template = job["template"][0]
    require(template["max_retries"] == 0 and template["timeout"] == "300s",
            "PostgreSQL owns retries; provider retries forbidden")
    require(template["service_account"] == "${google_service_account.worker.email}",
            "worker must use dedicated identity")
    container = template["containers"][0]
    require(container["image"] == "${var.worker_image}", "worker must use approved digest variable")
    require(container["name"] == "worker" and
            container["command"] == ["python", "-m", "engine.cloud_worker"] and
            container["args"] == [], "worker template cannot embed caller commands")
    require(container["resources"][0]["limits"] == {"cpu": "1", "memory": "2Gi"},
            "worker resource ceiling changed without capacity review")
    require({x["name"] for x in container["env"]} ==
            {"G3_WORKER_DSN", "G3_SCRATCH_ROOT", "G3_OBJECT_STORE", "G3_GCS_BUCKET"},
            "worker environment must be allowlisted")
    for name, service in resource["google_cloud_run_v2_service"].items():
        require(service["ingress"] == "INGRESS_TRAFFIC_INTERNAL_ONLY" and
                service.get("invoker_iam_disabled") is False, "HTTP cloud templates require internal ingress and IAM")
        service_template = service["template"][0]
        require(service_template["scaling"] == [{"min_instance_count": 0, "max_instance_count": 2}],
                "HTTP instance limits must start at zero and cap at two")
        require(service_template["max_instance_request_concurrency"] <= 4,
                "HTTP request concurrency exceeds local admission design")
        require(all(c["image"] == "${var." + name + "_image}" for c in service_template["containers"]),
                "service images must use digest-validated variables")
    for name in ("api_image", "orchestrator_image", "worker_image"):
        definition = variables[name]
        require("default" not in definition and "@sha256:[a-f0-9]{64}" in definition["validation"][0]["condition"],
                "runtime image digest must be required")
    invokers = resource["google_cloud_run_v2_service_iam_member"]
    require(len(invokers) == 1 and invokers["task_caller_only"]["name"] ==
            "${google_cloud_run_v2_service.orchestrator.name}" and
            invokers["task_caller_only"]["member"] == "serviceAccount:${google_service_account.task_caller.email}",
            "only task caller can invoke orchestrator; API has no cloud invoker")
    launches = resource["google_cloud_run_v2_job_iam_member"]
    require(len(launches) == 2 and launches["orchestrator_launch"]["role"] ==
            "roles/run.jobsExecutorWithOverrides" and
            launches["orchestrator_launch"]["member"] == "serviceAccount:${google_service_account.orchestrator.email}",
            "only admission controller may launch the exact worker job")
    require(all(x["name"] == "${google_cloud_run_v2_job.worker.name}" and
                x["member"] == "serviceAccount:${google_service_account.orchestrator.email}"
                for x in launches.values()), "job observation must remain exact job/controller only")
    require(set(roles["execution_observer"]["permissions"]) == {"run.executions.get"} and
            set(roles["operation_observer"]["permissions"]) == {"run.operations.get"},
            "observation roles must contain read-only terminal proof permissions")
    for _, _, obj in flattened:
        for key, value in obj.items():
            require(key not in {"secret_data", "private_key", "credentials", "access_token"},
                    "secret payloads and credentials forbidden in Terraform")
        for env in _environment(obj):
            if "DSN" in env.get("name", ""):
                require("value" not in env and "value_source" in env,
                        "DSN must be an existing Secret Manager reference")
    budget = resource["google_billing_budget"]["sandbox"]
    require({x["threshold_percent"] for x in budget["threshold_rules"]} == {.5, .9, 1.0},
            "50/90/100 percent budget alerts required")
    require(all(rule.get("disable_default_iam_recipients", False) is False
                for rule in budget.get("all_updates_rule", [])),
            "billing administrators must receive delayed budget alerts")
    require(variables["output_retention_days"].get("default") == 30,
            "default documented output retention intent is30 days")


def _environment(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "env":
                yield from child
            else:
                yield from _environment(child)
    elif isinstance(value, list):
        for child in value:
            yield from _environment(child)


if __name__ == "__main__":
    audit(load_configuration(Path(__file__).parent))
    print("PASS: offline JSON security policy; separate toolchain evidence required; Google Cloud NOT_VALIDATED")
