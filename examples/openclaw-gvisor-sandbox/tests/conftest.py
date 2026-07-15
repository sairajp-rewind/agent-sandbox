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

"""Shared test fakes and autouse leak-check fixtures for OpenClaw test suite."""

import asyncio
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import aiohttp
import aiohttp.web
import pytest
from kubernetes import client as k8s_client_mod
from kubernetes import config as k8s_config


class FakeSandbox:
    def __init__(self, name: str = "test-sandbox", namespace: str = "default"):
        self.name = name
        self.namespace = namespace
        self.spec_operating_mode = "Running"
        self.status_conditions: List[Dict[str, Any]] = []
        self.annotations: Dict[str, str] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<FakeSandbox {self.namespace}/{self.name} mode={self.spec_operating_mode}>"

    def set_operating_mode(self, mode: str) -> None:
        with self._lock:
            self.spec_operating_mode = mode

    def set_condition(
        self,
        type: str,
        status: str,
        reason: str = "",
        message: str = "",
    ) -> None:
        with self._lock:
            cond = {
                "type": type,
                "status": status,
                "reason": reason,
                "message": message,
                "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            # replace if existing
            self.status_conditions = [
                c for c in self.status_conditions if c["type"] != type
            ]
            self.status_conditions.append(cond)

    def set_annotation(self, key: str, value: str) -> None:
        with self._lock:
            self.annotations[key] = value

    def to_object(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "apiVersion": "agents.x-k8s.io/v1beta1",
                "kind": "Sandbox",
                "metadata": {
                    "name": self.name,
                    "namespace": self.namespace,
                    "annotations": dict(self.annotations),
                },
                "spec": {
                    "operatingMode": self.spec_operating_mode,
                },
                "status": {
                    "conditions": list(self.status_conditions),
                },
            }

    def assert_no_leaks(self) -> None:
        pass


class FakeLLM:
    def __init__(self):
        self.prompts_received: List[Dict[str, Any]] = []
        self.response_queue: List[Dict[str, Any]] = []
        self.base_url: str = ""
        self._lock = threading.Lock()
        self.aiohttp_app = aiohttp.web.Application()
        self.aiohttp_app.router.add_post(
            "/v1/chat/completions", self._handle_completions
        )

    def __repr__(self) -> str:
        return f"<FakeLLM base_url={self.base_url} received={len(self.prompts_received)}>"

    def push_response(self, text: str, **kwargs: Any) -> None:
        with self._lock:
            resp = {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": kwargs.get("model", "fake-claude"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": text,
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            self.response_queue.append(resp)

    def last_prompt(self) -> Dict[str, Any]:
        with self._lock:
            if not self.prompts_received:
                return {}
            return self.prompts_received[-1]

    async def _handle_completions(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        data = await request.json()
        with self._lock:
            self.prompts_received.append(data)
            if self.response_queue:
                resp = self.response_queue.pop(0)
            else:
                resp = {
                    "id": "chatcmpl-default",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": data.get("model", "fake-claude"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
        return aiohttp.web.json_response(resp)

    def assert_no_leaks(self) -> None:
        pass


class FakeOpenClaw:
    def __init__(self):
        self.queue_size: int = 0
        self.pending_replies: int = 0
        self.active_embedded_runs: int = 0
        self.active_tasks: int = 0
        self.next_run_time: Optional[str] = None
        self.base_url: str = ""
        self.linked_llm: Optional[FakeLLM] = None
        self.startup_delay_seconds: float = 0.0
        self._start_time = time.time()
        self._lock = threading.Lock()
        self.token_env_var: Optional[str] = None
        self.is_paired: bool = False

        self.aiohttp_app = aiohttp.web.Application()
        self.aiohttp_app.router.add_get("/", self._handle_root)
        self.aiohttp_app.router.add_get("/v1/health/idle", self._handle_idle)
        self.aiohttp_app.router.add_get("/v1/cron/next", self._handle_cron_next)
        self.aiohttp_app.router.add_post("/api/v1/chat", self._handle_chat)
        self.aiohttp_app.router.add_get(
            "/api/v1/lifecycle/status", self._handle_idle
        )

    def __repr__(self) -> str:
        return f"<FakeOpenClaw base_url={self.base_url} pending={self.pending_count}>"

    @property
    def pending_count(self) -> int:
        with self._lock:
            return (
                self.queue_size
                + self.pending_replies
                + self.active_embedded_runs
                + self.active_tasks
            )

    def set_counter(self, name: str, value: int) -> None:
        with self._lock:
            if hasattr(self, name):
                setattr(self, name, value)

    def _check_auth(self, request: aiohttp.web.Request) -> bool:
        if self.token_env_var is None:
            return True
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {self.token_env_var}"
        return auth_header == expected

    async def _handle_root(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            return aiohttp.web.Response(status=401, text="Unauthorized")
        elapsed = time.time() - self._start_time
        if elapsed < self.startup_delay_seconds:
            await asyncio.sleep(self.startup_delay_seconds - elapsed)
        return aiohttp.web.Response(status=200, text="OpenClaw Gateway Ready")

    async def _handle_idle(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            return aiohttp.web.Response(status=401, text="Unauthorized")
        with self._lock:
            p_count = (
                self.queue_size
                + self.pending_replies
                + self.active_embedded_runs
                + self.active_tasks
            )
            data = {
                "idle": p_count == 0,
                "pendingCount": p_count,
                "details": {
                    "queueSize": self.queue_size,
                    "pendingReplies": self.pending_replies,
                    "activeEmbeddedRuns": self.active_embedded_runs,
                    "activeTasks": self.active_tasks,
                },
            }
        return aiohttp.web.json_response(data)

    async def _handle_cron_next(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            return aiohttp.web.Response(status=401, text="Unauthorized")
        with self._lock:
            return aiohttp.web.json_response({"nextRunTime": self.next_run_time})

    async def _handle_chat(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            return aiohttp.web.Response(status=401, text="Unauthorized")
        if not self.is_paired:
            return aiohttp.web.Response(status=403, text="Device not paired")
        data = await request.json()
        if self.linked_llm and self.linked_llm.base_url:
            async with aiohttp.ClientSession() as session:
                llm_payload = {
                    "model": "fake-claude",
                    "messages": [{"role": "user", "content": str(data.get("message", ""))}],
                }
                async with session.post(
                    f"{self.linked_llm.base_url}/v1/chat/completions",
                    json=llm_payload,
                ) as resp:
                    llm_data = await resp.json()
                    content = llm_data["choices"][0]["message"]["content"]
        else:
            content = "echo: " + str(data.get("message", ""))

        return aiohttp.web.json_response(
            {"response": content, "status": "ok", "timestamp": int(time.time())}
        )

    def assert_no_leaks(self) -> None:
        assert self.pending_count == 0, f"FakeOpenClaw leaked pending items: {self.pending_count}"


class FakeLifecycleDaemon:
    def __init__(self, k8s_client: Optional[Any] = None):
        self.k8s_client = k8s_client
        self.patch_log: List[Dict[str, Any]] = []
        self.scripted_status: Dict[str, Any] = {"operatingMode": "Running"}
        self._lock = threading.Lock()
        self.base_url: str = ""
        self.max_idle_seconds: float = 0.0
        self._polling_task: Optional[asyncio.Task] = None

        self.aiohttp_app = aiohttp.web.Application()
        self.aiohttp_app.router.add_post("/v1/sandbox/suspend", self._handle_suspend)
        self.aiohttp_app.router.add_post("/v1/sandbox/resume", self._handle_resume)
        self.aiohttp_app.router.add_get("/v1/sandbox/status", self._handle_status)

    def __repr__(self) -> str:
        mode = "live" if self.k8s_client else "unit"
        return f"<FakeLifecycleDaemon mode={mode} patches={len(self.patch_log)}>"

    async def start_polling(
        self,
        openclaw_url: str,
        sandbox_name: str = "test-sandbox",
        namespace: str = "default",
        interval: float = 0.05,
    ) -> None:
        """Start background task polling openclaw /v1/health/idle to auto-trigger suspend."""
        if self._polling_task is not None:
            self._polling_task.cancel()
        self._polling_task = asyncio.create_task(
            self._poll_loop(openclaw_url, sandbox_name, namespace, interval)
        )

    async def stop_polling(self) -> None:
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

    async def _poll_loop(
        self,
        openclaw_url: str,
        sandbox_name: str,
        namespace: str,
        interval: float,
    ) -> None:
        idle_since: Optional[float] = None
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(openclaw_url + "/v1/health/idle") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("idle", False):
                                if idle_since is None:
                                    idle_since = time.time()
                                elif (time.time() - idle_since) >= self.max_idle_seconds:
                                    with self._lock:
                                        if self.scripted_status["operatingMode"] != "Suspended":
                                            self.patch_log.append(
                                                {"name": sandbox_name, "namespace": namespace, "op": "suspend"}
                                            )
                                            self.scripted_status["operatingMode"] = "Suspended"
                                            if self.k8s_client:
                                                self.k8s_client.patch_namespaced_custom_object(
                                                    group="agents.x-k8s.io",
                                                    version="v1beta1",
                                                    plural="sandboxes",
                                                    namespace=namespace,
                                                    name=sandbox_name,
                                                    body={"spec": {"operatingMode": "Suspended"}},
                                                )
                            else:
                                idle_since = None
                except Exception:
                    pass
                await asyncio.sleep(interval)

    async def _handle_suspend(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        data = await request.json()
        name = data.get("name", "test-sandbox")
        ns = data.get("namespace", "default")
        with self._lock:
            self.patch_log.append({"name": name, "namespace": ns, "op": "suspend"})
            self.scripted_status["operatingMode"] = "Suspended"
        if self.k8s_client:
            self.k8s_client.patch_namespaced_custom_object(
                group="agents.x-k8s.io",
                version="v1beta1",
                plural="sandboxes",
                namespace=ns,
                name=name,
                body={"spec": {"operatingMode": "Suspended"}},
            )
        return aiohttp.web.json_response({"status": "suspended"})

    async def _handle_resume(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        data = await request.json()
        name = data.get("name", "test-sandbox")
        ns = data.get("namespace", "default")
        with self._lock:
            self.patch_log.append({"name": name, "namespace": ns, "op": "resume"})
            self.scripted_status["operatingMode"] = "Running"
        if self.k8s_client:
            self.k8s_client.patch_namespaced_custom_object(
                group="agents.x-k8s.io",
                version="v1beta1",
                plural="sandboxes",
                namespace=ns,
                name=name,
                body={"spec": {"operatingMode": "Running"}},
            )
        return aiohttp.web.json_response({"status": "resumed"})

    async def _handle_status(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        with self._lock:
            return aiohttp.web.json_response(dict(self.scripted_status))

    def assert_no_leaks(self) -> None:
        if self._polling_task is not None:
            self._polling_task.cancel()
            self._polling_task = None


class FakeSandboxRouter:
    def __init__(self):
        self.buffered: List[Dict[str, Any]] = []
        self.on_wake_needed: Optional[Callable[[str], None]] = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<FakeSandboxRouter buffered={len(self.buffered)}>"

    def receive_ws_handshake(self, sandbox_name: str, sandbox_mode: str = "Suspended") -> Dict[str, Any]:
        with self._lock:
            handle = {"id": f"ws-{len(self.buffered)+1}", "sandbox": sandbox_name, "status": "buffered"}
            if sandbox_mode == "Suspended":
                self.buffered.append(handle)
                if self.on_wake_needed:
                    self.on_wake_needed(sandbox_name)
            else:
                handle["status"] = "forwarded"
            return handle

    def mark_ready(self, sandbox_name: str) -> None:
        with self._lock:
            for h in self.buffered:
                if h["sandbox"] == sandbox_name:
                    h["status"] = "forwarded"
            self.buffered = [h for h in self.buffered if h["sandbox"] != sandbox_name]

    def assert_no_leaks(self) -> None:
        assert self.buffered == [], f"FakeSandboxRouter leaked buffered handshakes: {self.buffered}"


class FakeK8s:
    def __init__(self):
        self._objects: Dict[tuple, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.custom = MagicMock()

    def __repr__(self) -> str:
        return f"<FakeK8s objects={len(self._objects)}>"

    def apply(self, obj: Dict[str, Any]) -> None:
        with self._lock:
            group, version = obj.get("apiVersion", "").split("/", 1) if "/" in obj.get("apiVersion", "") else ("", obj.get("apiVersion", ""))
            kind = obj.get("kind", "")
            plural = kind.lower() + ("es" if kind.endswith("x") or kind.endswith("s") else "s")
            meta = obj.get("metadata", {})
            ns = meta.get("namespace", "default")
            name = meta.get("name", "")
            self._objects[(group, plural, ns, name)] = obj

    def get(self, group: str, plural: str, ns: str, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._objects.get((group, plural, ns, name))

    def patch(self, group: str, plural: str, ns: str, name: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            obj = self._objects.get((group, plural, ns, name))
            if not obj:
                return None
            if "spec" in patch:
                obj.setdefault("spec", {}).update(patch["spec"])
            if "status" in patch:
                obj.setdefault("status", {}).update(patch["status"])
            return obj

    def delete(self, group: str, plural: str, ns: str, name: str) -> None:
        with self._lock:
            self._objects.pop((group, plural, ns, name), None)

    def assert_no_leaks(self) -> None:
        pass


_ACTIVE_FAKES: List[Any] = []


@pytest.fixture
async def fake_openclaw():
    app = FakeOpenClaw()
    runner = aiohttp.web.AppRunner(app.aiohttp_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1] if site._server and site._server.sockets else 0
    app.base_url = f"http://127.0.0.1:{port}"
    _ACTIVE_FAKES.append(app)
    yield app
    await runner.cleanup()


@pytest.fixture
async def fake_llm():
    app = FakeLLM()
    runner = aiohttp.web.AppRunner(app.aiohttp_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1] if site._server and site._server.sockets else 0
    app.base_url = f"http://127.0.0.1:{port}"
    _ACTIVE_FAKES.append(app)
    yield app
    await runner.cleanup()


@pytest.fixture
async def fake_daemon():
    app = FakeLifecycleDaemon()
    runner = aiohttp.web.AppRunner(app.aiohttp_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1] if site._server and site._server.sockets else 0
    app.base_url = f"http://127.0.0.1:{port}"
    _ACTIVE_FAKES.append(app)
    yield app
    await app.stop_polling()
    await runner.cleanup()


@pytest.fixture
def fake_router():
    router = FakeSandboxRouter()
    _ACTIVE_FAKES.append(router)
    yield router


@pytest.fixture(autouse=True)
async def _leak_check():
    yield
    for f in _ACTIVE_FAKES:
        if hasattr(f, "stop_polling"):
            await f.stop_polling()
        f.assert_no_leaks()
    _ACTIVE_FAKES.clear()


@pytest.fixture(scope="session")
def kube_client():
    k8s_config.load_kube_config()
    return k8s_client_mod.ApiClient()
