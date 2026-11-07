"""Security regression tests for a review-only cloud topology.

No Terraform, credentials, Docker, emulator or cloud service is invoked here.
Negative cases model real privilege, resource-cap and retention mistakes.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[2]
IAC = ROOT / "infra" / "g3a"
_spec = importlib.util.spec_from_file_location("g3a_iac_policy", IAC / "audit_policy.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)


class CloudInfrastructurePolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = policy.load_configuration(IAC)

    def reject(self, mutation):
        changed = copy.deepcopy(self.config)
        mutation(changed)
        with self.assertRaises(policy.PolicyViolation):
            policy.audit(changed)

    def test_reviewable_topology_satisfies_local_security_policy(self):
        policy.audit(self.config)

    def test_unreviewed_hcl_sidecar_cannot_bypass_json_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "unreviewed.tf").write_text('resource "google_project_iam_member" "bad" {}')
            with self.assertRaises(policy.PolicyViolation):
                policy.load_configuration(Path(directory))

    def test_provider_credentials_and_remote_state_are_rejected(self):
        self.reject(lambda c: c["provider"]["google"].update(credentials="fixture-not-a-real-key"))
        self.reject(lambda c: c["terraform"].update(backend={"gcs": {"bucket": "some-remote-state"}}))

    def test_provider_pin_cannot_float_without_review(self):
        self.reject(lambda c: c["terraform"]["required_providers"]["google"].update(version=">=8.0.0"))

    def test_review_guard_cannot_be_preapproved(self):
        self.reject(lambda c: c["variable"]["staging_authorized"].update(default=True))

    def test_unreviewed_runtime_artifact_gate_cannot_be_removed(self):
        self.reject(lambda c: c["resource"]["terraform_data"]["staging_authorization"]
                    ["lifecycle"].update(precondition=[]))

    def test_side_resource_cannot_bypass_authorization(self):
        self.reject(lambda c: c["resource"]["google_cloud_tasks_queue"]["media"].pop("depends_on"))

    def test_public_media_is_rejected(self):
        self.reject(lambda c: c["resource"]["google_storage_bucket"]["media"]
                    .update(public_access_prevention="inherited"))

    def test_destructive_bucket_teardown_is_rejected(self):
        self.reject(lambda c: c["resource"]["google_storage_bucket"]["media"].update(force_destroy=True))

    def test_expiry_must_not_blindly_delete_authoritative_objects(self):
        self.reject(lambda c: c["resource"]["google_storage_bucket"]["media"]
                    ["lifecycle_rule"].append({"action": [{"type": "Delete"}], "condition": [{"age": 30}]}))

    def test_public_invocation_and_broad_project_iam_are_rejected(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_service_iam_member"]
                    ["task_caller_only"].update(member="allUsers"))
        self.reject(lambda c: c["resource"].update(google_project_iam_member={
            "bad": {"project": "prod", "role": "roles/editor", "member": "user:operator@example.invalid"}}))

    def test_bucket_binding_cannot_be_elevated_to_storage_admin(self):
        self.reject(lambda c: c["resource"]["google_storage_bucket_iam_member"]["worker"]
                    .update(role="roles/storage.admin"))

    def test_token_minting_cannot_be_granted_to_api_or_worker(self):
        self.reject(lambda c: c["resource"]["google_service_account_iam_member"]
                    ["tasks_agent_can_attach_task_caller"].update(
                        role="roles/iam.serviceAccountTokenCreator"))
        self.reject(lambda c: c["resource"]["google_service_account_iam_member"]
                    ["tasks_agent_can_attach_task_caller"].update(
                        service_account_id="${google_service_account.worker.name}"))

    def test_runtime_image_cannot_bypass_digest_validation_variable(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0]["containers"][0]
                    .update(image="example.invalid/worker:latest"))

    def test_project_observation_cannot_gain_list_or_mutation_permissions(self):
        self.reject(lambda c: c["resource"]["google_project_iam_custom_role"]
                    ["operation_observer"]["permissions"].append("run.operations.list"))
        self.reject(lambda c: c["resource"]["google_project_iam_member"]
                    ["orchestrator_operation_observer"].update(
                        member="serviceAccount:${google_service_account.api.email}"))

    def test_long_lived_cloud_keys_are_rejected(self):
        self.reject(lambda c: c["resource"].update(google_service_account_key={"bad": {}}))

    def test_api_cannot_gain_object_deletion_or_listing(self):
        self.reject(lambda c: c["resource"]["google_project_iam_custom_role"]
                    ["api_media"]["permissions"].append("storage.objects.delete"))

    def test_worker_cannot_update_existing_objects(self):
        self.reject(lambda c: c["resource"]["google_project_iam_custom_role"]
                    ["worker_media"]["permissions"].append("storage.objects.update"))

    def test_queue_cannot_launch_unbounded_signals(self):
        self.reject(lambda c: c["resource"]["google_cloud_tasks_queue"]["media"]
                    ["rate_limits"][0].update(max_concurrent_dispatches=100))

    def test_queue_provider_retries_remain_bounded(self):
        self.reject(lambda c: c["resource"]["google_cloud_tasks_queue"]["media"]
                    ["retry_config"][0].update(max_attempts=-1))

    def test_platform_retries_cannot_bypass_postgresql_retry_authority(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0].update(max_retries=3))

    def test_provider_fanout_cannot_multiply_one_admitted_launch(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0].update(task_count=50, parallelism=50))

    def test_native_worker_resource_ceiling_cannot_silently_increase(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0]["containers"][0]
                    ["resources"][0].update(limits={"cpu": "8", "memory": "32Gi"}))

    def test_worker_cannot_receive_arbitrary_command_or_environment(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0]["containers"][0].update(command=["sh", "-c"]))
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0]["containers"][0]
                    ["env"].append({"name": "LD_PRELOAD", "value": "/tmp/evil.so"}))

    def test_cloud_http_cannot_disable_iam_or_expose_public_ingress(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_service"]["orchestrator"]
                    .update(invoker_iam_disabled=True))
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_service"]["api"]
                    .update(ingress="INGRESS_TRAFFIC_ALL"))

    def test_service_instances_must_start_at_zero_and_remain_bounded(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_service"]["api"]
                    ["template"][0].update(scaling=[{"min_instance_count": 1, "max_instance_count": 100}]))

    def test_runtime_tags_and_default_images_are_rejected(self):
        self.reject(lambda c: c["variable"]["worker_image"].update(default="example.invalid/worker:latest"))

    def test_plaintext_dsn_in_terraform_is_rejected(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job"]["worker"]
                    ["template"][0]["template"][0]["containers"][0]
                    ["env"][0].update(value="postgres://fixture:fixture@db.example.invalid/render"))

    def test_only_orchestrator_identity_can_launch_worker(self):
        self.reject(lambda c: c["resource"]["google_cloud_run_v2_job_iam_member"]
                    ["orchestrator_launch"].update(member="serviceAccount:${google_service_account.api.email}"))

    def test_budget_alerts_must_keep_all_three_thresholds(self):
        self.reject(lambda c: c["resource"]["google_billing_budget"]["sandbox"]
                    .update(threshold_rules=[{"threshold_percent": 1.0}]))

    def test_container_proposal_is_nonroot_and_does_not_download_or_ship_binary(self):
        dockerfile = (IAC / "Dockerfile.worker.PROPOSAL").read_text()
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("ARG REVIEWED_RUNTIME_IMAGE", dockerfile)
        self.assertNotIn("RUN ", dockerfile)
        self.assertNotIn("ADD ", dockerfile)
        self.assertIn("NOT BUILT", dockerfile)


if __name__ == "__main__":
    unittest.main()
