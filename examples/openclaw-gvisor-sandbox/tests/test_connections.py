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

"""Group 6 tests: Connection handling, WebSocket tracking, and wake-on-traffic via router."""

import asyncio
import aiohttp
import pytest
import subprocess
from _helpers import (
    nodeport_url,
    require_paired_json,
    get_openclaw_pod_name,
)


# ==============================================================================
# Unit Contract Tests (No cluster required, runs by default)
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_ws_connections_track_pending_arithmetically(fake_openclaw):
    """Verify concurrent WS connections track pending count arithmetic accurately."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(fake_openclaw.base_url + "/ws") as ws1, \
                   session.ws_connect(fake_openclaw.base_url + "/ws") as ws2, \
                   session.ws_connect(fake_openclaw.base_url + "/ws") as ws3:
            await asyncio.sleep(0.05)
            async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
                data = await resp.json()
                assert data["pendingCount"] == 3
                assert data["idle"] is False

        # After connections close, count returns to 0
        await asyncio.sleep(0.05)
        async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
            data = await resp.json()
            assert data["pendingCount"] == 0
            assert data["idle"] is True


@pytest.mark.asyncio
async def test_ws_disconnect_returns_endpoint_to_idle_after_grace(fake_openclaw):
    """Verify endpoint transitions back to idle=True once WS disconnects."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(fake_openclaw.base_url + "/ws") as ws:
            await asyncio.sleep(0.05)
            async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
                data = await resp.json()
                assert data["pendingCount"] == 1
                assert data["idle"] is False

        # After ws closes
        await asyncio.sleep(0.05)
        async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
            data = await resp.json()
            assert data["pendingCount"] == 0
            assert data["idle"] is True


def test_wake_on_traffic_buffers_and_replays_ws_handshake(fake_router):
    """Verify router buffers WS handshake when sandbox is Suspended and replays when ready."""
    from conftest import FakeSandbox
    sandbox = FakeSandbox(name="my-openclaw-sb", namespace="default")
    sandbox.set_operating_mode("Suspended")

    wake_calls = []
    fake_router.on_wake_needed = lambda sb_name: wake_calls.append(sb_name)

    # Receive WS handshake while sandbox is Suspended
    handle = fake_router.receive_ws_handshake(sandbox.name, sandbox_mode=sandbox.spec_operating_mode)

    assert handle["status"] == "buffered"
    assert len(fake_router.buffered) == 1
    assert wake_calls == ["my-openclaw-sb"]

    # Mark sandbox ready (simulating daemon resume completion)
    sandbox.set_operating_mode("Running")
    fake_router.mark_ready(sandbox.name)

    # Verify buffered handle replayed and cleared
    assert fake_router.buffered == []
    assert handle["status"] == "forwarded"


# ==============================================================================
# Live Behavior Tests (Requires cluster, run with -m live)
# ==============================================================================

@pytest.mark.live
@pytest.mark.skip(reason="TODO: deploy real sandbox-router/ + FakeLifecycleDaemon(k8s_client=...) live mode (plan.md § 4.4 Group 6)")
def test_wake_on_traffic_via_real_sandbox_router():
    """Verify real sandbox-router buffers WS traffic to a suspended pod and triggers resume via daemon stand-in."""
    pass


@pytest.mark.live
@pytest.mark.asyncio
async def test_ws_closed_client_side_on_pvc_mode_suspend():
    """Verify active client-side WebSocket receives disconnect when Sandbox is suspended."""
    pod_name = get_openclaw_pod_name()
    require_paired_json(pod_name)

    # Resolve sandbox name
    res = subprocess.run(
        ["kubectl", "get", "sandboxclaim", "openclaw-sandbox-claim", "-o", "jsonpath={.status.sandbox.name}"],
        text=True, capture_output=True, check=True
    )
    sandbox_name = res.stdout.strip()
    assert sandbox_name, "SandboxClaim has no assigned Sandbox"

    """
    NOTE: assumes gateway WS at /ws — this is our FakeOpenClaw's contract.
    Verify against OpenClaw v2026.3.23 docs before running live; path may
    differ.
    """
    # Open a WebSocket connection to the gateway
    ws_url = nodeport_url().replace("http://", "ws://") + "/ws"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=5) as ws:
                # Patch Sandbox to Suspended
                subprocess.run(
                    ["kubectl", "patch", "sandbox", sandbox_name, "--type=merge", "-p", '{"spec":{"operatingMode":"Suspended"}}'],
                    check=True, capture_output=True
                )

                # Expect WebSocket to receive close message or disconnect
                msg = await ws.receive(timeout=30)
                assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR)
    finally:
        # Patch Sandbox back to Running for cluster cleanup
        subprocess.run(
            ["kubectl", "patch", "sandbox", sandbox_name, "--type=merge", "-p", '{"spec":{"operatingMode":"Running"}}'],
            check=False, capture_output=True
        )
