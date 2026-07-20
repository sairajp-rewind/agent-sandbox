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

"""Group 3 tests: Pod Snapshot (CRIU / GKE Pod Snapshot Controller) memory and process preservation."""

import asyncio
import json
import shlex
import time
import aiohttp
import requests
import pytest
import subprocess
from _helpers import wait_until, kubectl_exec, nodeport_url

pytestmark = pytest.mark.live


@pytest.fixture(scope="session", autouse=True)
def _require_pod_snapshot_crds():
    """Skip every test in this module if the Pod Snapshot Controller isn't installed."""
    try:
        from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient
        PodSnapshotSandboxClient()
    except Exception as e:
        pytest.skip(f"Pod Snapshot Controller / CRDs not available on cluster: {e}")


def _get_snapshot_sandbox():
    """Retrieve SandboxWithSnapshotSupport for the test openclaw SandboxClaim."""
    from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient
    client = PodSnapshotSandboxClient()
    return client.get_sandbox(claim_name="openclaw-sandbox-claim")


def test_openclaw_process_pid_preserved_across_snapshot():
    """Verify OpenClaw's node process is preserved by CRIU across snapshot+resume.

    OpenClaw's `node dist/index.js gateway ...` renames its process title to
    `openclaw-gateway` once fully initialized. Since it's in the main container's
    PID tree, gVisor's CRIU checkpoint preserves it — matching PID after resume
    proves the process was restored from snapshot rather than restarted.
    """
    sandbox = _get_snapshot_sandbox()
    pod_name = sandbox.get_pod_name()

    # Wait for OpenClaw to have fully initialized (process title renamed to openclaw-gateway)
    def _openclaw_ready():
        try:
            return kubectl_exec(pod_name, ["pgrep", "-f", "openclaw-gateway"]).strip() != ""
        except Exception:
            return False

    wait_until(_openclaw_ready, timeout=60, interval=2.0, message="OpenClaw process didn't reach openclaw-gateway state")

    pid_before = kubectl_exec(pod_name, ["pgrep", "-f", "openclaw-gateway"]).strip()
    assert pid_before, "openclaw-gateway process not found"

    res_suspend = sandbox.suspend(snapshot_before_suspend=True)
    assert res_suspend.success, f"Suspend failed: {res_suspend.error_reason}"

    res_resume = sandbox.resume()
    assert res_resume.success, f"Resume failed: {res_resume.error_reason}"
    assert res_resume.restored_from_snapshot is True

    resumed_pod = sandbox.get_pod_name()
    pid_after = kubectl_exec(resumed_pod, ["pgrep", "-f", "openclaw-gateway"]).strip()
    assert pid_after == pid_before, (
        f"openclaw-gateway PID changed ({pid_before} → {pid_after}): process was restarted, not restored from snapshot"
    )


def _gateway_status_code(pod_name, timeout_seconds=3):
    """Return HTTP status code as string from OpenClaw gateway root, or empty on error."""
    try:
        return kubectl_exec(pod_name, ["sh", "-c",
            f"curl -s -o /dev/null -w '%{{http_code}}' --max-time {timeout_seconds} "
            f"http://127.0.0.1:18789/"
        ]).strip()
    except Exception:
        return ""


def test_openclaw_gateway_responsive_immediately_after_resume():
    """Verify OpenClaw gateway responds without cold-start delay after resume.

    Uses `kubectl exec + curl` inside the pod to avoid dependence on
    external cluster network reachability from the test host.
    """
    sandbox = _get_snapshot_sandbox()
    pod_name = sandbox.get_pod_name()

    # Baseline: wait for gateway to be ready inside the pod
    wait_until(
        lambda: _gateway_status_code(pod_name) == "200",
        timeout=60, interval=2.0,
        message="Gateway not returning 200 from inside the pod before snapshot",
    )

    # Suspend + snapshot + resume
    res_suspend = sandbox.suspend(snapshot_before_suspend=True)
    assert res_suspend.success, f"Suspend failed: {res_suspend.error_reason}"

    res_resume = sandbox.resume()
    assert res_resume.success, f"Resume failed: {res_resume.error_reason}"
    assert res_resume.restored_from_snapshot is True

    resumed_pod = sandbox.get_pod_name()

    # Immediately after resume, gateway should respond fast — cold-start
    # would take 5-10s. If it responds in <3s (kubectl exec + curl overhead
    # is ~500ms-1s), the snapshot skipped the boot cycle.
    start = time.time()
    code = _gateway_status_code(resumed_pod, timeout_seconds=3)
    elapsed = time.time() - start

    assert code == "200", f"Gateway returned '{code}' instead of 200 after resume"
    assert elapsed < 3.0, (
        f"Gateway took {elapsed:.2f}s to respond after resume — likely "
        f"cold-started, not snapshot-restored"
    )


@pytest.mark.asyncio
@pytest.mark.skip(reason="Assumes gateway WebSocket endpoint at /ws")
async def test_snapshot_preserves_open_websocket():
    """Verify active client-side WebSocket state remains connected across snapshot suspend and resume.

    Note: Runs on clusters where Pod Snapshot Controller preserves pod IP or ingress routing across restore.
    """
    sandbox = _get_snapshot_sandbox()
    ws_url = nodeport_url().replace("http://", "ws://") + "/ws"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, timeout=10) as ws:
            # Suspend and resume sandbox while WebSocket connection is active
            loop = asyncio.get_running_loop()
            res_suspend = await loop.run_in_executor(None, lambda: sandbox.suspend(snapshot_before_suspend=True))
            assert res_suspend.success, f"Suspend failed: {res_suspend.error_reason}"

            res_resume = await loop.run_in_executor(None, lambda: sandbox.resume())
            assert res_resume.success, f"Resume failed: {res_resume.error_reason}"
            assert res_resume.restored_from_snapshot is True

            # Verify WebSocket connection is intact by pinging post-resume
            await ws.ping()


def test_pod_restored_condition_becomes_true_after_resume():
    """Verify Pod status condition PodRestored=True on the backing Pod via host kubectl after resume."""
    sandbox = _get_snapshot_sandbox()

    res_suspend = sandbox.suspend(snapshot_before_suspend=True)
    assert res_suspend.success, f"Suspend failed: {res_suspend.error_reason}"

    res_resume = sandbox.resume()
    assert res_resume.success, f"Resume failed: {res_resume.error_reason}"
    assert res_resume.restored_from_snapshot is True

    # Query the backing Pod object directly from the host system using host kubectl
    def _pod_restored_condition_true():
        try:
            pod_name = sandbox.get_pod_name()
            res = subprocess.run(
                ["kubectl", "get", "pod", pod_name, "-o", "jsonpath={.status.conditions}"],
                text=True, capture_output=True, timeout=10
            )
            if res.returncode != 0:
                return False
            return '"type":"PodRestored"' in res.stdout and '"status":"True"' in res.stdout
        except Exception:
            return False

    wait_until(_pod_restored_condition_true, timeout=60, interval=1.0, message="Pod status condition PodRestored=True not found")


def test_restore_from_specific_snapshot_uid():
    """Verify Sandbox can be restored from the older of two snapshots by UID."""
    sandbox = _get_snapshot_sandbox()
    pod_name = sandbox.get_pod_name()

    try:
        # Step 1: Write marker A to PVC and create Snapshot A
        kubectl_exec(pod_name, ["sh", "-c", "echo A > /workspace/marker.txt"])
        snap_A = sandbox.snapshots.create("snapshot-older-A")
        assert snap_A.success, f"Snapshot A creation failed: {snap_A.error_reason}"
        uid_A = snap_A.snapshot_uid

        # Step 2: Overwrite marker with B and create Snapshot B
        kubectl_exec(pod_name, ["sh", "-c", "echo B > /workspace/marker.txt"])
        snap_B = sandbox.snapshots.create("snapshot-newer-B")
        assert snap_B.success, f"Snapshot B creation failed: {snap_B.error_reason}"

        # Step 3: Suspend without taking another snapshot
        suspend_res = sandbox.suspend(snapshot_before_suspend=False)
        assert suspend_res.success, f"Suspend failed: {suspend_res.error_reason}"

        # Step 4: Restore specifically to Snapshot A
        restore_res = sandbox.restore(snapshot_uid=uid_A)
        assert restore_res.success, f"Restore failed: {restore_res.error_reason}"
        assert restore_res.restored_from_snapshot is True
        assert restore_res.snapshot_uid == uid_A

        # Step 5: Verify marker file contains "A", proving restoration targeted the older snapshot
        resumed_pod = sandbox.get_pod_name()
        marker = kubectl_exec(resumed_pod, ["cat", "/workspace/marker.txt"]).strip()
        assert marker == "A", f"Restore didn't target older snapshot; expected 'A', got '{marker}'"
    finally:
        try:
            resumed_pod = sandbox.get_pod_name()
            kubectl_exec(resumed_pod, ["rm", "-f", "/workspace/marker.txt"])
        except Exception:
            pass
