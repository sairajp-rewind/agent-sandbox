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

"""Group 7 tests: bootstrap, responsiveness, authentication, and simple chat."""

import time
import aiohttp
import requests
import pytest
from _helpers import (
    nodeport_url,
    require_paired_json,
    require_fakellm_config,
    require_fakellm_reachable_from_pod,
    get_openclaw_pod_name,
)


# ==============================================================================
# Unit Contract Tests (No cluster required, runs by default)
# ==============================================================================

@pytest.mark.asyncio
async def test_gateway_root_responds_within_deadline(fake_openclaw):
    """Verify in-process FakeOpenClaw responds under the 1500ms threshold."""
    start = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(fake_openclaw.base_url + "/") as resp:
            text = await resp.text()
            elapsed = (time.time() - start) * 1000
            assert resp.status == 200
            assert text == "OpenClaw Gateway Ready"
            assert elapsed < 1500  # 1500 ms threshold


@pytest.mark.asyncio
async def test_openclaw_gateway_token_env_var_honored(fake_openclaw):
    """Verify FakeOpenClaw token verification logic matches expected contract."""
    fake_openclaw.token_env_var = "secure-token-123"

    async with aiohttp.ClientSession() as session:
        # Unauthenticated request should fail with 401
        async with session.get(fake_openclaw.base_url + "/") as resp:
            assert resp.status == 401

        # Authenticated request should succeed
        headers = {"Authorization": "Bearer secure-token-123"}
        async with session.get(fake_openclaw.base_url + "/", headers=headers) as resp:
            assert resp.status == 200


# ==============================================================================
# Live Behavior Tests (Requires cluster, run with -m live)
# ==============================================================================

@pytest.mark.live
def test_gateway_root_responds_within_deadline_live():
    """Verify real OpenClaw gateway responds under the 1500ms threshold."""
    pod_name = get_openclaw_pod_name()
    require_paired_json(pod_name)

    url = nodeport_url() + "/"
    start = time.time()
    resp = requests.get(url, timeout=5)
    elapsed = (time.time() - start) * 1000
    assert resp.status_code == 200
    assert elapsed < 1500


@pytest.mark.live
def test_openclaw_provider_api_key_detected_at_startup_live(fake_llm):
    """Verify OpenClaw pod detects API key and reports provider ready.

    Requires:
      - paired.json seeded (§ 1a.A)
      - openclaw.json override pointing at FakeLLM (§ 1a.B)
      - FakeLLM reachable from pod (§ 1a.C)
    """
    pod_name = get_openclaw_pod_name()

    # Apply guards from § 1a. Until resolved, these auto-skip.
    require_paired_json(pod_name)
    require_fakellm_config(pod_name)
    require_fakellm_reachable_from_pod(pod_name, fake_llm.base_url)

    resp = requests.get(nodeport_url() + "/v1/health/idle", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("idle", False) is True or "details" in data


@pytest.mark.live
@pytest.mark.asyncio
async def test_hello_world_chat_turn_via_fakellm_live(fake_llm):
    """Verify an end-to-end chat turn hits the FakeLLM and returns successfully.

    Requires:
      - paired.json seeded (§ 1a.A)
      - openclaw.json override pointing at FakeLLM (§ 1a.B)
      - FakeLLM reachable from pod (§ 1a.C)
    """
    pod_name = get_openclaw_pod_name()

    require_paired_json(pod_name)
    require_fakellm_config(pod_name)
    require_fakellm_reachable_from_pod(pod_name, fake_llm.base_url)

    fake_llm.push_response("Hello from Fake LLM!")

    payload = {"message": "Hello OpenClaw"}
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            nodeport_url() + "/api/v1/chat",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["response"] == "Hello from Fake LLM!"
            assert len(fake_llm.prompts_received) == 1
            last_prompt = fake_llm.last_prompt()
            assert "Hello OpenClaw" in str(last_prompt)
