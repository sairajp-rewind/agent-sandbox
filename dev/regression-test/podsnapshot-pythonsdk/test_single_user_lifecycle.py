"""
Verifies the complete PodSnapshotSandboxClient lifecycle for a single user in an isolated namespace.
Required operations: Create sandbox, Create snapshot, List snapshot, Suspend/Resume with pod snapshot, Delete snapshot.
"""

import time
import pytest


class TestSingleUserLifecycle:

    def test_single_user_full_lifecycle(self, single_user_client, env_vars, wait_for_snapshot_ready):
        namespace = env_vars["tenant_single_ns"]
        warmpool = env_vars["warm_pool_name"]

        print(f"\n--- 1. Creating Sandbox in Namespace '{namespace}' using Warmpool '{warmpool}' ---")
        sandbox = single_user_client.create_sandbox(warmpool=warmpool, namespace=namespace)
        assert sandbox is not None, "Failed to instantiate Sandbox handle"

        # Allow sandbox container to reach steady-state on gVisor before checkpointing
        print("Waiting 10 seconds for sandbox pod container state to stabilize...")
        time.sleep(10)

        try:
            print("\n--- 2. Creating Sequential Manual Snapshots ---")
            snap1_resp = sandbox.snapshots.create("single-user-snap-1")
            assert snap1_resp.success, f"Snapshot 1 failed: {snap1_resp.error_reason}"
            snap1_uid = snap1_resp.snapshot_uid
            assert snap1_uid, "Snapshot 1 UID missing"
            print(f"Snapshot 1 created with UID: {snap1_uid}")

            time.sleep(5)

            snap2_resp = sandbox.snapshots.create("single-user-snap-2")
            assert snap2_resp.success, f"Snapshot 2 failed: {snap2_resp.error_reason}"
            snap2_uid = snap2_resp.snapshot_uid
            assert snap2_uid, "Snapshot 2 UID missing"
            print(f"Snapshot 2 created with UID: {snap2_uid}")

            print("\n--- 3. Listing & Verifying Initial Snapshots ---")
            assert wait_for_snapshot_ready(sandbox, snap2_uid), f"Snapshot 2 '{snap2_uid}' did not become ready in time"

            list_resp = sandbox.snapshots.list()
            assert list_resp.success, f"Snapshot list failed: {list_resp.error_reason}"
            uids = [s.snapshot_uid for s in list_resp.snapshots]
            assert snap1_uid in uids, f"Snapshot 1 '{snap1_uid}' not found in list {uids}"
            assert snap2_uid in uids, f"Snapshot 2 '{snap2_uid}' not found in list {uids}"
            print(f"Verified initial snapshots list: {uids}")

            print("\n--- 4. Suspending Sandbox with Checkpoint Snapshot ---")
            suspend_resp = sandbox.suspend(snapshot_before_suspend=True)
            assert suspend_resp.success, f"Suspend failed: {suspend_resp.error_reason}"
            assert sandbox.is_suspended(), "Sandbox should be suspended after suspend()"
            
            assert suspend_resp.snapshot_response is not None, "Suspend response missing snapshot_response"
            suspend_snap_uid = suspend_resp.snapshot_response.snapshot_uid
            assert suspend_snap_uid, "Suspend checkpoint snapshot UID missing"
            print(f"Sandbox suspended! Checkpoint snapshot UID: {suspend_snap_uid}")

            # Wait for checkpoint snapshot to be ready in snapshot controller before resuming
            assert wait_for_snapshot_ready(sandbox, suspend_snap_uid), f"Checkpoint snapshot '{suspend_snap_uid}' did not become ready in time"

            print("\n--- 5. Resuming Sandbox from Checkpoint ---")
            resume_resp = sandbox.resume()
            assert resume_resp.success, f"Resume failed: {resume_resp.error_reason}"
            assert resume_resp.restored_from_snapshot, "Sandbox should have been restored from snapshot"
            assert not sandbox.is_suspended(), "Sandbox should not be suspended after resume"
            assert resume_resp.snapshot_uid == suspend_snap_uid, f"Expected resume from checkpoint '{suspend_snap_uid}', got '{resume_resp.snapshot_uid}'"
            print(f"Sandbox resumed! Verified restoration from checkpoint UID: {resume_resp.snapshot_uid}")

            print("\n--- 6. Verifying Snapshot Count & Executing Deletions ---")
            pre_delete_list = sandbox.snapshots.list()
            assert pre_delete_list.success, f"Snapshot list before delete failed: {pre_delete_list.error_reason}"
            assert len(pre_delete_list.snapshots) == 3, f"Expected 3 snapshots (snap1, snap2, checkpoint), found {len(pre_delete_list.snapshots)}"
            assert {snap1_uid, snap2_uid, suspend_snap_uid} == {s.snapshot_uid for s in pre_delete_list.snapshots}

            del1_resp = sandbox.snapshots.delete(snap1_uid)
            assert del1_resp.success, f"Failed to delete snapshot 1: {del1_resp.error_reason}"

            del_all_resp = sandbox.snapshots.delete_all()
            assert del_all_resp.success, f"Delete all failed: {del_all_resp.error_reason}"

            final_list = sandbox.snapshots.list()
            assert len(final_list.snapshots) == 0, f"Expected 0 snapshots after delete_all, found {len(final_list.snapshots)}"
            print("All snapshots cleanly deleted.")

        finally:
            print("\n--- 7. Terminating Sandbox ---")
            sandbox.terminate()
