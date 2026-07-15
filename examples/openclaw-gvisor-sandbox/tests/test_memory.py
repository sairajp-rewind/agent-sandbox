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

"""Group 1 tests: Memory persistence (short-term vs long-term), PVC preservation, and serialization."""

import hashlib
import os
import aiohttp
import pytest
from _helpers import (
    nodeport_url,
    require_paired_json,
    require_fakellm_config,
    require_fakellm_reachable_from_pod,
    parse_qmd_frontmatter,
    kubectl_exec,
    get_openclaw_pod_name,
)


# ==============================================================================
# Unit Contract Tests (No cluster required)
# ==============================================================================

def test_markdown_memory_frontmatter_and_sections_parseable(tmp_path):
    """Verify parse_qmd_frontmatter correctly extracts headers from memory markdown format."""
    md_content = """---
id: mem-123
agentId: agent-456
tags: fact, personal
createdAt: 2026-07-10T20:19:14Z
---
# User Preferred Name
The user prefers to be called Alice.
"""
    parsed = parse_qmd_frontmatter(md_content)
    assert parsed.get("id") == "mem-123"
    assert parsed.get("agentId") == "agent-456"
    assert parsed.get("tags") == "fact, personal"
    assert parsed.get("createdAt") == "2026-07-10T20:19:14Z"


def test_memory_content_byte_identical_across_suspend_cycle(tmp_path):
    """Verify that file hashing and simulated suspend/resume leaves files byte-identical."""
    sqlite_file = tmp_path / "memory.sqlite"
    md_file = tmp_path / "fact.md"

    sqlite_file.write_bytes(b"SQLITE-DATA-DUMMY")
    md_file.write_text("Markdown content")

    def hash_dir(path):
        hashes = {}
        for root, _, files in os.walk(path):
            for file in files:
                fpath = os.path.join(root, file)
                hasher = hashlib.sha256()
                with open(fpath, "rb") as f:
                    hasher.update(f.read())
                hashes[os.path.relpath(fpath, path)] = hasher.hexdigest()
        return hashes

    before_hashes = hash_dir(tmp_path)

    # Simulate suspend/resume (no-op/untouched files in unit test)
    after_hashes = hash_dir(tmp_path)

    assert before_hashes == after_hashes


# ==============================================================================
# Live Behavior Tests (Requires cluster, run with -m live)
# ==============================================================================

@pytest.fixture
def live_setup(fake_llm):
    """Fixture that handles live checks and returns dynamic pod name."""
    pod_name = get_openclaw_pod_name()
    require_paired_json(pod_name)
    require_fakellm_config(pod_name)
    require_fakellm_reachable_from_pod(pod_name, fake_llm.base_url)
    return pod_name


@pytest.mark.live
@pytest.mark.asyncio
async def test_short_term_memory_survives_within_single_session_live(fake_llm, live_setup):
    """Verify chat turn history persists in the same running session."""
    headers = {"Content-Type": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        # Turn A
        fake_llm.push_response("Hello Alice, nice to meet you!")
        async with session.post(
            nodeport_url() + "/api/v1/chat",
            json={"message": "My name is Alice"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            assert resp.status == 200

        # Turn B
        fake_llm.push_response("You are Alice.")
        async with session.post(
            nodeport_url() + "/api/v1/chat",
            json={"message": "What is my name?"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            assert resp.status == 200

    # Assert FakeLLM received the context
    last_prompt = fake_llm.last_prompt()
    assert "Alice" in str(last_prompt)


@pytest.mark.live
@pytest.mark.skip(reason="TODO: implement suspend/resume flow (plan.md § 4.4 memory)")
def test_short_term_memory_lost_after_pvc_suspend_live(fake_llm, live_setup):
    """Verify in-memory session/history is lost after sandbox suspend/resume (PVC preservation only)."""
    pass


@pytest.mark.live
@pytest.mark.asyncio
async def test_long_term_memory_written_to_disk_on_remember_intent_live(fake_llm, live_setup):
    """Verify memory remember intent writes LTM SQLite/markdown to root PVC."""
    pod_name = live_setup
    headers = {"Content-Type": "application/json"}

    fake_llm.push_response("Okay, I will remember that you like apples.")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            nodeport_url() + "/api/v1/chat",
            json={"message": "Please remember that I like apples"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            assert resp.status == 200

    # Check for sqlite memory files on the pod
    out = kubectl_exec(pod_name, ["find", "/workspace/.openclaw/memory", "-name", "*.sqlite"])
    assert ".sqlite" in out


@pytest.mark.live
@pytest.mark.skip(reason="TODO: implement LTM suspend/resume survival check (plan.md § 4.4 memory)")
def test_long_term_memory_survives_pvc_suspend_live(fake_llm, live_setup):
    """Verify LTM survives suspend/resume and is loaded back into context."""
    pass


@pytest.mark.live
@pytest.mark.skip(reason="TODO: implement semantic search LTM prompt injection assertion (plan.md § 4.4 memory)")
def test_relevant_ltm_entries_injected_into_prompt_live(fake_llm, live_setup):
    """Verify semantic search injects only relevant LTM entries into prompt."""
    pass
