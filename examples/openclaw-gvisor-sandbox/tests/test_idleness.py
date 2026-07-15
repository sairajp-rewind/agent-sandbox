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

"""Group 5 tests: OpenClaw idle detection and auto-suspend lifecycle daemon integration."""

import asyncio
import aiohttp
import pytest


@pytest.mark.asyncio
async def test_idle_endpoint_reports_true_on_fresh_boot(fake_openclaw):
    """Verify OpenClaw reports idle on boot before any task is registered."""
    async with aiohttp.ClientSession() as session:
        async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["idle"] is True
            assert data["pendingCount"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "q, r, e, t, expected_count, expected_idle",
    [
        (0, 0, 0, 0, 0, True),
        (5, 0, 0, 0, 5, False),
        (0, 2, 0, 0, 2, False),
        (0, 0, 3, 0, 3, False),
        (0, 0, 0, 4, 4, False),
        (5, 2, 3, 4, 14, False),
    ],
)
async def test_idle_endpoint_pendingcount_matches_component_sum(
    fake_openclaw, q, r, e, t, expected_count, expected_idle
):
    """Verify pending count equals the sum of the individual component queues.

    WATCH-OUT: We must reset counters back to 0 in the finally block
    to avoid triggering the autouse leak-check fixture.
    """
    try:
        fake_openclaw.set_counter("queue_size", q)
        fake_openclaw.set_counter("pending_replies", r)
        fake_openclaw.set_counter("active_embedded_runs", e)
        fake_openclaw.set_counter("active_tasks", t)

        async with aiohttp.ClientSession() as session:
            async with session.get(fake_openclaw.base_url + "/v1/health/idle") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["pendingCount"] == expected_count
                assert data["idle"] is expected_idle
    finally:
        # Reset counters to 0 to satisfy autouse leak-check
        fake_openclaw.set_counter("queue_size", 0)
        fake_openclaw.set_counter("pending_replies", 0)
        fake_openclaw.set_counter("active_embedded_runs", 0)
        fake_openclaw.set_counter("active_tasks", 0)


@pytest.mark.asyncio
async def test_lifecycle_daemon_calls_suspend_after_max_idle_time(fake_openclaw, fake_daemon):
    """Verify daemon invokes suspend API when idle period exceeds the threshold."""
    fake_daemon.max_idle_seconds = 0.2
    fake_openclaw.set_counter("queue_size", 0)

    assert len(fake_daemon.patch_log) == 0

    await fake_daemon.start_polling(fake_openclaw.base_url)
    await asyncio.sleep(0.4)

    assert len(fake_daemon.patch_log) == 1
    assert fake_daemon.patch_log[0]["op"] == "suspend"


@pytest.mark.asyncio
async def test_lifecycle_daemon_does_not_suspend_while_pending(fake_openclaw, fake_daemon):
    """Verify daemon does not call suspend API if there are pending operations."""
    fake_daemon.max_idle_seconds = 0.2
    try:
        fake_openclaw.set_counter("active_tasks", 1)
        await fake_daemon.start_polling(fake_openclaw.base_url)
        await asyncio.sleep(0.4)

        assert len(fake_daemon.patch_log) == 0
    finally:
        fake_openclaw.set_counter("active_tasks", 0)
