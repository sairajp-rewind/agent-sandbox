# Copyright 2026 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Verifies concurrent lifecycle operations and cross-tenant Workload Identity isolation across independent namespaces.
"""

import time
import subprocess
import pytest


class TestMultiUserLifecycle:

    def test_multi_user_concurrent_lifecycle_and_isolation(self, multi_user_client, env_vars, wait_for_snapshot_ready):
        ns_alpha = env_vars["tenant_alpha_ns"]
        ns_beta = env_vars["tenant_beta_ns"]
        warmpool = env_vars["warm_pool_name"]

        print(f"\n--- 1. Provisioning Concurrent Sandboxes: Tenant Alpha ({ns_alpha}) & Tenant Beta ({ns_beta}) ---")
        sandbox_alpha = multi_user_client.create_sandbox(warmpool=warmpool, namespace=ns_alpha)
        sandbox_beta = multi_user_client.create_sandbox(warmpool=warmpool, namespace=ns_beta)

        assert sandbox_alpha is not None, "Failed to instantiate Tenant Alpha Sandbox"
        assert sandbox_beta is not None, "Failed to instantiate Tenant Beta Sandbox"

        # Allow container state on gVisor to reach steady-state before checkpointing
        print("Waiting 10 seconds for tenant sandbox containers to stabilize...")
        time.sleep(10)

        try:
            print("\n--- 2. Triggering Independent Snapshots ---")
            snap_alpha_resp = sandbox_alpha.snapshots.create("alpha-snap-1")
            assert snap_alpha_resp.success, f"Alpha snapshot failed: {snap_alpha_resp.error_reason}"
            uid_alpha = snap_alpha_resp.snapshot_uid
            assert uid_alpha, "Alpha snapshot UID missing"

            snap_beta_resp = sandbox_beta.snapshots.create("beta-snap-1")
            assert snap_beta_resp.success, f"Beta snapshot failed: {snap_beta_resp.error_reason}"
            uid_beta = snap_beta_resp.snapshot_uid
            assert uid_beta, "Beta snapshot UID missing"

            print(f"Tenant Alpha Snapshot UID: {uid_alpha}")
            print(f"Tenant Beta Snapshot UID: {uid_beta}")

            print("\n--- 3. Verifying Cross-Tenant Snapshot Isolation ---")
            assert wait_for_snapshot_ready(sandbox_alpha, uid_alpha), f"Alpha snapshot '{uid_alpha}' not ready"
            assert wait_for_snapshot_ready(sandbox_beta, uid_beta), f"Beta snapshot '{uid_beta}' not ready"

            list_alpha = sandbox_alpha.snapshots.list()
            list_beta = sandbox_beta.snapshots.list()

            assert list_alpha.success, f"Alpha list failed: {list_alpha.error_reason}"
            assert list_beta.success, f"Beta list failed: {list_beta.error_reason}"

            uids_alpha = [s.snapshot_uid for s in list_alpha.snapshots]
            uids_beta = [s.snapshot_uid for s in list_beta.snapshots]

            # Assert Tenant Alpha only sees Tenant Alpha's snapshots
            assert uid_alpha in uids_alpha, f"Alpha UID missing from Alpha list: {uids_alpha}"
            assert uid_beta not in uids_alpha, f"SECURITY VIOLATION: Beta UID {uid_beta} visible in Alpha list: {uids_alpha}"

            # Assert Tenant Beta only sees Tenant Beta's snapshots
            assert uid_beta in uids_beta, f"Beta UID missing from Beta list: {uids_beta}"
            assert uid_alpha not in uids_beta, f"SECURITY VIOLATION: Alpha UID {uid_alpha} visible in Beta list: {uids_beta}"

            # RBAC Verification: Prove default-deny RBAC posture for tenant-alpha KSA in tenant-beta namespace
            print("\n--- 3b. Verifying Default-Deny RBAC Posture via 'kubectl auth can-i' ---")
            auth_check = subprocess.run([
                "kubectl", "auth", "can-i", "list", "podsnapshots.podsnapshot.gke.io",
                "-n", ns_beta,
                "--as", f"system:serviceaccount:{ns_alpha}:sandbox-sa"
            ], capture_output=True, text=True)

            assert auth_check.returncode in (0, 1), f"kubectl auth can-i execution failed (exit code {auth_check.returncode}): {auth_check.stderr}"
            can_i_result = auth_check.stdout.strip().lower()
            assert "no" in can_i_result, f"RBAC SECURITY VIOLATION: Tenant Alpha KSA can list podsnapshots in Tenant Beta namespace ({ns_beta}): {can_i_result}"
            print("Default-deny RBAC posture verified: 'kubectl auth can-i' returned 'no'.")

            print("\n--- 4. Cross-Tenant Deletion Prohibition Check ---")
            # Tenant Alpha attempting to delete Tenant Beta's snapshot must not delete Beta's snapshot
            unauth_del_resp = sandbox_alpha.snapshots.delete(uid_beta)
            assert len(unauth_del_resp.deleted_snapshots) == 0, f"Alpha should not have deleted Beta's snapshot, got: {unauth_del_resp.deleted_snapshots}"

            list_beta_after_del = sandbox_beta.snapshots.list()
            assert uid_beta in [s.snapshot_uid for s in list_beta_after_del.snapshots], "SECURITY VIOLATION: Beta snapshot missing after Alpha's cross-tenant delete attempt!"
            print("Cross-tenant deletion prohibition confirmed.")

            print("\n--- 5. Cross-Tenant Restore Prohibition Check ---")
            # Suspend Alpha without taking a new snapshot
            sus_alpha_no_snap = sandbox_alpha.suspend(snapshot_before_suspend=False)
            assert sus_alpha_no_snap.success, f"Alpha suspend failed: {sus_alpha_no_snap.error_reason}"
            assert sandbox_alpha.is_suspended(), "Alpha sandbox should be suspended"

            # Tenant Alpha attempting to restore Tenant Beta's snapshot must be rejected
            unauth_restore = sandbox_alpha.restore(snapshot_uid=uid_beta)
            assert not unauth_restore.success, "SECURITY VIOLATION: Tenant Alpha was able to restore Tenant Beta's snapshot!"
            err_msg = unauth_restore.error_reason.lower() if unauth_restore.error_reason else ""
            assert "does not exist" in err_msg or "not found" in err_msg, f"Expected non-existent snapshot error, got: {unauth_restore.error_reason}"
            print("Cross-tenant restore prohibition confirmed.")

            # Resume Alpha using its own original snapshot
            res_alpha_restore = sandbox_alpha.restore(snapshot_uid=uid_alpha)
            assert res_alpha_restore.success, f"Alpha restore to own snapshot failed: {res_alpha_restore.error_reason}"
            assert not sandbox_alpha.is_suspended(), "Alpha sandbox should be running after restore"

            print("\n--- 6. Symmetric Suspend & Resume for Both Tenants ---")
            # Suspend Alpha & Beta independently with split assertions
            sus_alpha = sandbox_alpha.suspend(snapshot_before_suspend=True)
            assert sus_alpha.success, f"Tenant Alpha suspend failed: {sus_alpha.error_reason}"

            sus_beta = sandbox_beta.suspend(snapshot_before_suspend=True)
            assert sus_beta.success, f"Tenant Beta suspend failed: {sus_beta.error_reason}"

            assert sus_alpha.snapshot_response is not None, "Alpha suspend response missing snapshot_response"
            assert sus_beta.snapshot_response is not None, "Beta suspend response missing snapshot_response"

            uid_checkpoint_alpha = sus_alpha.snapshot_response.snapshot_uid
            uid_checkpoint_beta = sus_beta.snapshot_response.snapshot_uid

            assert uid_checkpoint_alpha, "Alpha checkpoint UID missing"
            assert uid_checkpoint_beta, "Beta checkpoint UID missing"

            assert wait_for_snapshot_ready(sandbox_alpha, uid_checkpoint_alpha), f"Alpha checkpoint '{uid_checkpoint_alpha}' not ready"
            assert wait_for_snapshot_ready(sandbox_beta, uid_checkpoint_beta), f"Beta checkpoint '{uid_checkpoint_beta}' not ready"

            # Resume Alpha & Beta independently with split assertions
            res_alpha = sandbox_alpha.resume()
            assert res_alpha.success, f"Tenant Alpha resume failed: {res_alpha.error_reason}"
            assert res_alpha.restored_from_snapshot, "Tenant Alpha should have been restored from snapshot"
            assert res_alpha.snapshot_uid == uid_checkpoint_alpha, f"Expected Alpha resume from '{uid_checkpoint_alpha}', got '{res_alpha.snapshot_uid}'"

            res_beta = sandbox_beta.resume()
            assert res_beta.success, f"Tenant Beta resume failed: {res_beta.error_reason}"
            assert res_beta.restored_from_snapshot, "Tenant Beta should have been restored from snapshot"
            assert res_beta.snapshot_uid == uid_checkpoint_beta, f"Expected Beta resume from '{uid_checkpoint_beta}', got '{res_beta.snapshot_uid}'"

            print("Symmetric suspend and resume for both Tenant Alpha and Tenant Beta confirmed.")

            print("\n--- 7. Independent Teardown ---")
            del_alpha = sandbox_alpha.snapshots.delete_all()
            assert del_alpha.success, f"Alpha delete_all failed: {del_alpha.error_reason}"

            del_beta = sandbox_beta.snapshots.delete_all()
            assert del_beta.success, f"Beta delete_all failed: {del_beta.error_reason}"

            final_list_alpha = sandbox_alpha.snapshots.list()
            final_list_beta = sandbox_beta.snapshots.list()
            assert len(final_list_alpha.snapshots) == 0, f"Expected 0 snapshots for Alpha, found {len(final_list_alpha.snapshots)}"
            assert len(final_list_beta.snapshots) == 0, f"Expected 0 snapshots for Beta, found {len(final_list_beta.snapshots)}"
            print("All multi-tenant snapshots cleanly deleted.")

        finally:
            print("\n--- 8. Terminating Tenant Sandboxes ---")
            sandbox_alpha.terminate()
            sandbox_beta.terminate()
