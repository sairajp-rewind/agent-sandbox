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

"""Helper utilities for OpenClaw sandbox test suite."""

import os
import shlex
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional
import pytest


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 30.0,
    interval: float = 0.5,
    message: str = "",
) -> None:
    """Poll a predicate until it returns True or timeout is reached."""
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(message or f"Timed out after {timeout} seconds waiting for condition")


def kubectl_exec(
    pod: str,
    cmd: List[str],
    namespace: str = "default",
    *,
    input: Optional[str] = None,
) -> str:
    """Run kubectl exec inside a pod and return stdout."""
    full_cmd = ["kubectl", "exec", "-n", namespace, pod, "--"] + cmd
    res = subprocess.run(
        full_cmd,
        input=input,
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout


def kubectl_apply(path: str, namespace: str = "default") -> None:
    """Apply a manifest via kubectl."""
    subprocess.run(
        ["kubectl", "apply", "-n", namespace, "-f", path],
        check=True,
        capture_output=True,
    )


def kubectl_delete(path: str, namespace: str = "default", ignore_missing: bool = True) -> None:
    """Delete a manifest via kubectl."""
    cmd = ["kubectl", "delete", "-n", namespace, "-f", path]
    if ignore_missing:
        cmd.append("--ignore-not-found=true")
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )


def canary_write(pod: str, path: str, value: str, namespace: str = "default") -> None:
    """Write a canary file inside a pod using kubectl exec."""
    cmd = f"echo -n {shlex.quote(value)} > {shlex.quote(path)}"
    kubectl_exec(pod, ["sh", "-c", cmd], namespace=namespace)


def canary_read(pod: str, path: str, namespace: str = "default") -> str:
    """Read a canary file from inside a pod using kubectl exec."""
    return kubectl_exec(pod, ["cat", path], namespace=namespace)


def require_paired_json(pod: str, namespace: str = "default") -> None:
    """Check if /workspace/.openclaw/devices/paired.json exists in target pod; skip if absent per § 1a."""
    try:
        kubectl_exec(pod, ["test", "-f", "/workspace/.openclaw/devices/paired.json"], namespace=namespace)
    except subprocess.CalledProcessError:
        pytest.skip(
            "paired.json not found in pod: seeding mechanism is deferred per plan.md § 1a. "
            "Manually seed or await resolution of § 1a to run this live test."
        )


def require_fakellm_config(pod: str, namespace: str = "default") -> None:
    """Check if openclaw.json contains 'fakellm' provider configuration; skip if absent per § 1a.B."""
    try:
        content = kubectl_exec(pod, ["cat", "/etc/openclaw/openclaw.json"], namespace=namespace)
        if "fakellm" not in content:
            raise ValueError("fakellm not configured")
    except (subprocess.CalledProcessError, ValueError):
        pytest.skip(
            "FakeLLM configuration not found in openclaw.json: "
            "planned override is deferred per plan.md § 1a.B. "
            "Awaiting configuration injection before this live test can run."
        )


def require_fakellm_reachable_from_pod(pod: str, base_url: str, namespace: str = "default") -> None:
    """Check if the pod can reach the FakeLLM endpoint at base_url; skip if unreachable per § 1a.C."""
    try:
        kubectl_exec(pod, ["curl", "-s", "--connect-timeout", "2", "-o", "/dev/null", base_url], namespace=namespace)
    except subprocess.CalledProcessError:
        pytest.skip(
            f"FakeLLM at {base_url} is unreachable from pod {pod}: "
            "routing configuration is deferred per plan.md § 1a.C. "
            "Ensure the pod can resolve and route to the test host before running live tests."
        )


def nodeport_url(port: int = 30789) -> str:
    """Get the gateway URL, honoring env overrides for GKE/external ingress."""
    if "OPENCLAW_TEST_GATEWAY_URL" in os.environ:
        return os.environ["OPENCLAW_TEST_GATEWAY_URL"].rstrip("/")
    return f"http://127.0.0.1:{port}"


def get_openclaw_pod_name(claim_name: str = "openclaw-sandbox-claim", namespace: str = "default") -> str:
    """Resolve backing pod name for an OpenClaw SandboxClaim; skip if unresolvable."""
    try:
        res = subprocess.run(
            ["kubectl", "get", "sandboxclaim", claim_name, "-n", namespace, "-o", "jsonpath={.status.sandbox.name}"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"kubectl get sandboxclaim {claim_name} timed out")
    except Exception as e:
        pytest.skip(f"kubectl get sandboxclaim {claim_name} failed: {e}")

    sandbox_name = res.stdout.strip()
    if not sandbox_name:
        pytest.skip(f"SandboxClaim {claim_name} not found or has no sandbox assigned — no cluster context?")

    try:
        res = subprocess.run(
            ["kubectl", "get", "sandbox", sandbox_name, "-n", namespace, "-o", "jsonpath={.metadata.annotations.agents\\.x-k8s\\.io/pod-name}"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"kubectl get sandbox {sandbox_name} timed out")
    except Exception as e:
        pytest.skip(f"kubectl get sandbox {sandbox_name} failed: {e}")

    pod_name = res.stdout.strip()
    return pod_name if pod_name else sandbox_name


def fake_llm_provider_block(base_url: str, model_id: str = "fake-claude") -> Dict[str, Any]:
    """Return an openclaw.json provider block pointing at FakeLLM."""
    return {
        "models": {
            "providers": {
                "fakellm": {
                    "baseUrl": base_url,
                    "apiKey": "sk-test-nonempty",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": model_id,
                            "name": "Fake Claude",
                            "input": ["text"],
                            "contextWindow": 200000,
                            "maxTokens": 8192,
                        }
                    ],
                },
            },
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": f"fakellm/{model_id}",
                }
            }
        },
    }


def parse_qmd_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from OpenClaw markdown memory files."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    frontmatter: Dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, val = line.split(":", 1)
            frontmatter[key.strip()] = val.strip()
    return frontmatter
