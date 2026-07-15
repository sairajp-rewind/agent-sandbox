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

"""Group 2 tests: PVC state preservation across pod deletes and suspend/resume cycles."""

import os
import uuid
import pytest
import subprocess
from _helpers import (
    canary_write,
    canary_read,
    get_openclaw_pod_name,
    wait_until,
    kubectl_exec,
    kubectl_apply,
    kubectl_delete,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module", autouse=True)
def _apply_openclaw_manifests():
    """Apply example manifests before tests, delete after unless OPENCLAW_TEST_KEEP_MANIFESTS=1."""
    if os.environ.get("OPENCLAW_TEST_KEEP_MANIFESTS") == "1":
        yield
        return

    example_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    manifests = ["openclaw-config.yaml", "openclaw-template.yaml", "openclaw-warmpool.yaml", "openclaw-claim.yaml"]

    for name in manifests:
        kubectl_apply(os.path.join(example_dir, name))

    def _claim_ready():
        try:
            get_openclaw_pod_name()
            return True
        except Exception:
            return False

    wait_until(_claim_ready, timeout=180, interval=2.0)
    yield

    for name in reversed(manifests):
        kubectl_delete(os.path.join(example_dir, name), ignore_missing=True)


def test_pvc_survives_pod_delete_and_respawn():
    """Verify data written to the PVC at /workspace/.openclaw persists when pod is deleted and respawned."""
    pod_name = get_openclaw_pod_name()
    canary_value = f"canary-{uuid.uuid4().hex[:8]}"
    canary_path = "/workspace/.openclaw/canary-test.txt"

    try:
        # Write canary file
        canary_write(pod_name, canary_path, canary_value)
        assert canary_read(pod_name, canary_path).strip() == canary_value

        # Capture old UID before deleting pod
        old_uid = subprocess.run(
            ["kubectl", "get", "pod", pod_name, "-o", "jsonpath={.metadata.uid}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        # Delete the backing pod to trigger controller respawn
        subprocess.run(["kubectl", "delete", "pod", pod_name, "--wait=true"], check=True, capture_output=True)

        # Wait until a new pod UID appears
        def _pod_respawned():
            res = subprocess.run(
                ["kubectl", "get", "pod", pod_name, "-o", "jsonpath={.metadata.uid}"],
                capture_output=True, text=True
            )
            return res.returncode == 0 and res.stdout.strip() != "" and res.stdout.strip() != old_uid

        wait_until(_pod_respawned, timeout=60, interval=2.0, message="New pod failed to respawn after deletion")

        # Wait for the respawned pod condition to be ready
        subprocess.run(["kubectl", "wait", f"pod/{pod_name}", "--for=condition=ready", "--timeout=180s"], check=True)

        new_pod = get_openclaw_pod_name()
        assert canary_read(new_pod, canary_path).strip() == canary_value
    finally:
        try:
            current_pod = get_openclaw_pod_name()
            kubectl_exec(current_pod, ["rm", "-f", canary_path])
        except Exception:
            pass


def test_pvc_survives_operating_mode_suspend_then_resume():
    """Verify PVC data survives switching Sandbox.spec.operatingMode to Suspended and back to Running."""
    pod_name = get_openclaw_pod_name()
    canary_value = f"canary-suspend-{uuid.uuid4().hex[:8]}"
    canary_path = "/workspace/.openclaw/canary-suspend.txt"

    # Resolve sandbox name
    res = subprocess.run(
        ["kubectl", "get", "sandboxclaim", "openclaw-sandbox-claim", "-o", "jsonpath={.status.sandbox.name}"],
        text=True, capture_output=True, check=True
    )
    sandbox_name = res.stdout.strip()
    assert sandbox_name, "SandboxClaim has no assigned Sandbox"

    try:
        # Write canary file
        canary_write(pod_name, canary_path, canary_value)

        # Patch Sandbox operatingMode to Suspended
        subprocess.run(
            ["kubectl", "patch", "sandbox", sandbox_name, "--type=merge", "-p", '{"spec":{"operatingMode":"Suspended"}}'],
            check=True, capture_output=True
        )

        # Wait for old pod to be deleted
        def _pod_deleted():
            res = subprocess.run(
                ["kubectl", "get", "pod", pod_name],
                capture_output=True, text=True
            )
            return "NotFound" in res.stderr or res.returncode != 0

        wait_until(_pod_deleted, timeout=40, interval=1.0, message="Pod was not deleted after patching operatingMode to Suspended")

        # Patch Sandbox operatingMode back to Running
        subprocess.run(
            ["kubectl", "patch", "sandbox", sandbox_name, "--type=merge", "-p", '{"spec":{"operatingMode":"Running"}}'],
            check=True, capture_output=True
        )

        # Wait for new pod to be ready and verify canary
        def _pod_resumed_and_ready():
            try:
                cur_pod = get_openclaw_pod_name()
                return canary_read(cur_pod, canary_path).strip() == canary_value
            except Exception:
                return False

        wait_until(_pod_resumed_and_ready, timeout=60, interval=2.0, message="Pod failed to resume with PVC canary intact")
    finally:
        try:
            current_pod = get_openclaw_pod_name()
            kubectl_exec(current_pod, ["rm", "-f", canary_path])
        except Exception:
            pass
