# OpenClaw Test Suite — Unified Implementation Plan (First PR)

**Status:** ready to implement.
**Spec of record:** [`TESTS_PROPOSAL.md`](TESTS_PROPOSAL.md) in this directory — what we're testing.
**This document:** how we're building it, in what order, with what code shapes.

A fresh implementer should be able to complete the first PR using this file alone. External references are listed but the plan does not require reading them beforehand.

---

## 0. Prerequisites for the implementer

- Local checkout of `kubernetes-sigs/agent-sandbox` at `main` (or your PR branch off `main`).
- Python `>= 3.10`.
- `kubectl` in `PATH`.
- For live tests only: `KUBECONFIG` pointing at a suitable cluster (see § 8).
- (Optional, for verifying OpenClaw source references): `gh` CLI authenticated.

Working directory for every command in this plan: `examples/openclaw-gvisor-sandbox/`.

---

## 1. Locked decisions (do not re-litigate)

| Decision | Value |
|---|---|
| Language / test runner | Python 3.10+, `pytest` |
| Distribution name (`pyproject.toml`) | `openclaw-sandbox-tests` |
| OpenClaw image (pinned in `openclaw-template.yaml`) | `ghcr.io/openclaw/openclaw:2026.3.23` |
| Live-test marker | `@pytest.mark.live`; excluded by default via `addopts = "-m 'not live'"` |
| Wake-on-traffic Lifecycle Daemon | Option (a): ~30-line test-side HTTP handler that patches `spec.operatingMode=Running`. Replaceable when the real daemon lands (plan PR 2). |
| Group 8 (ClusterLoader2 recipes) | Deferred to next PR. Ship only `tests/loadtest/README.md` stub. |
| `tests/__init__.py` | Not created (matches `examples/agent-sandbox-rl/` convention). |
| `paired.json` seeding | **OPEN — deferred, see § 1a.** Live tests that need pairing (memory-behavior, chat, wake-on-traffic) auto-skip with a clear message when `paired.json` isn't present in the target pod. Mechanism to seed it is not decided in this PR. |
| Test-tier discipline | **Unit tests are contract-only** — they verify our fakes match wire formats and file layouts we believe OpenClaw uses. Anything asserting on OpenClaw's actual behavior (prompt content, chat response shape, memory retrieval) is `@pytest.mark.live` and runs against a real OpenClaw pod. |
| Live-test cluster selection | Standard `KUBECONFIG` env var; documented in `tests/README.md`. |
| Live-test manifests | Reuse the existing `openclaw-*.yaml` in this example dir via `kubectl apply -f`. Do not duplicate. |
| `run-test-kind.sh` | Kept as-is; add a one-line header comment pointing at `tests/` for the fuller suite. |

Source-of-truth for anything not covered above: [`TESTS_PROPOSAL.md`](TESTS_PROPOSAL.md).

---

## 1a. Open questions — test-infra ↔ pod bridging (must be resolved before live behavior tests can pass end-to-end)

Three related "how does test-only state get into or reach the pod, without modifying shipped manifests?" problems. Bundled here because they share the same design space; resolving one likely shapes the others.

### A. `/workspace/.openclaw/devices/paired.json` seeding

OpenClaw reads `paired.json` at process start. Options considered; none locked:
- (a) `kubectl exec ... tee` after pod boot, then delete-and-await-recreate. Requires restart orchestration.
- (b) Test-only template variant at `tests/manifests/openclaw-template-test.yaml` with an added init-container writing paired.json. Live tests apply this variant instead of the shipped template.
- (c) Extend the shipped `openclaw-template.yaml` with a conditional init-container that no-ops unless a test ConfigMap exists. Touches the shipped example.

**Note on uid 1000 (post-hardening, commit `4872f65`):** the container runs as `runAsUser: 1000` with `fsGroup: 1000`, so any seeding path must write as uid 1000 (or a group covered by fsGroup=1000). `kubectl exec` runs as uid 1000 by default and PVC writes succeed via fsGroup, but `/workspace/.openclaw/devices/` may need `mkdir -p` first. An init-container in a test-only template variant must also declare `runAsUser: 1000, runAsGroup: 1000, capabilities.drop: [ALL]` to match the main container's security context.

### B. `openclaw.json` override for `FakeLLM` provider block

The shipped template mounts a fixed-name ConfigMap `openclaw-config` (see `openclaw-config.yaml`) into the pod at `/etc/openclaw/openclaw.json`. Live bootstrap/memory tests need to inject a `models.providers.fakellm.{baseUrl,apiKey,api,models}` block so OpenClaw talks to our in-process `FakeLLM` instead of a real provider. Same three-option shape as (A):
- (a) Session fixture replaces the shipped ConfigMap in-place with the same name, restores on teardown. Mutates cluster state; concurrent test runs conflict.
- (b) Test-only ConfigMap + a template variant at `tests/manifests/openclaw-template-test.yaml` that mounts the test ConfigMap instead. Same variant that (A.b) proposes — consolidate.
- (c) Kustomize overlay under `tests/manifests/` that patches the shipped template's `configMap.name`.

### C. `FakeLLM` reachability from inside the pod

`FakeLLM` binds `127.0.0.1:0` on the test host. That address is **not reachable from inside a pod** — from the pod's POV, `127.0.0.1` is its own loopback. The `baseUrl` in the override from (B) must point somewhere the pod can actually reach:
- **kind on Docker Desktop (macOS / Windows):** `http://host.docker.internal:PORT` — works out of the box.
- **kind on Linux:** the docker bridge gateway (typically `172.17.0.1`, or the kind network gateway). Ugly and platform-sniffed.
- **GKE:** does not work at all. `FakeLLM` on the test host is not reachable from a cluster pod without a tunnel (reverse SSH, IAP, or similar).
- **Alternative:** deploy `FakeLLM` as an in-cluster Service (`http://fakellm.testing.svc:PORT`) — most portable but adds cluster-side deployment. Kills the "in-process, fast" property of the fake.

### Related — resolved conventions (not open, just documenting so tests don't reinvent)

- **Reaching the real OpenClaw gateway from tests:** use **NodePort 30789** as already configured in [`kind-service.yaml`](kind-service.yaml). `kubectl port-forward` is broken under gVisor (see the `## Known limitations` in [`README.md`](README.md)) so tests MUST NOT use it. On GKE the NodePort is exposed via LoadBalancer per [`gke-service.yaml`](gke-service.yaml); an IAP tunnel maps a local port to it for tests.
- **PVC-only tests (Group 2) don't hit any of A/B/C** — they just need `kubectl` access to patch/watch Sandbox CRs and exec into the pod for the canary. Those tests are unaffected by these open questions.

### Implementation impact until A, B, C are decided

Every live test that depends on OpenClaw actually running and talking to our fake LLM must auto-skip. Concretely, these tests need `require_paired_json`, `require_fakellm_config`, and `require_fakellm_reachable_from_pod` guards (one each per open question) with skip reasons pointing back to this section:

- `test_gateway_root_responds_within_deadline_live` — needs paired.json (A) only.
- `test_openclaw_provider_api_key_detected_at_startup_live` — needs A, B, C.
- `test_hello_world_chat_turn_via_fakellm_live` — needs A, B, C.
- All Group 1 live memory tests — need A, B, C.
- `test_wake_on_traffic_via_real_sandbox_router` — needs A only (chat isn't exercised, just WS lifecycle).
- Group 3 snapshot tests — don't hit any of A/B/C (they exercise sandbox lifecycle, not chat).
- Group 2 PVC live tests — unaffected.

When each open question resolves, the corresponding `require_*` guard is deleted and the tests light up.

---

## 2. Files to create

```
examples/openclaw-gvisor-sandbox/
├── pyproject.toml                        NEW
└── tests/                                NEW
    ├── conftest.py                       NEW — six fakes + autouse leak-check fixtures
    ├── _helpers.py                       NEW — wait_until, canary_write, canary_read, kubectl helpers
    ├── README.md                         NEW — how to run unit vs live tests
    ├── test_bootstrap.py                 NEW — Group 7 (4 tests)
    ├── test_memory.py                    NEW — Group 1 (7 tests)
    ├── test_pvc_preservation.py          NEW — Group 2 (2 live tests)
    ├── test_snapshot_preservation.py     NEW — Group 3 (5 live tests)
    ├── test_idleness.py                  NEW — Group 5 (4 tests)
    ├── test_connections.py               NEW — Group 6 (5 tests: 3 unit + 2 live)
    ├── test_cron_gateway.py              NEW — Group 4 (skip-stubs only)
    └── loadtest/
        └── README.md                     NEW — Group 8 stub for next PR
```

## 3. Files to touch (minimal edits)

- `examples/openclaw-gvisor-sandbox/README.md` — add a short **Tests** section at the bottom pointing at `tests/README.md`.
- `examples/openclaw-gvisor-sandbox/run-test-kind.sh` — add a one-line comment near the top: `# See tests/ for the fuller Python test suite; this script is the minimal shell smoke path.`

**Do not modify** `openclaw-template.yaml`, `openclaw-warmpool.yaml`, `openclaw-claim.yaml`, `openclaw-config.yaml`, `kind-config.yaml`, `kind-service.yaml`, `gke-service.yaml`. The pytest suite adapts to these; they do not adapt to it.

---

## 4. Detailed file specs

### 4.1 `pyproject.toml`

Full contents (adjust year and version as needed):

```toml
# Copyright 2026 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ...

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "openclaw-sandbox-tests"
version = "0.1.0.dev0"
description = "Test suite for the OpenClaw + Agent Sandbox integration example."
readme = "tests/README.md"
requires-python = ">=3.10"
classifiers = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
]
dependencies = []                              # runtime deps go under [test]

[project.optional-dependencies]
test = [
    "pytest>=7.0",
    "pytest-asyncio",
    "aiohttp",
    "requests",
    "kubernetes",
    "k8s-agent-sandbox",                       # provides PodSnapshotSandboxClient
]

[tool.setuptools.packages.find]
where = ["."]
exclude = ["tests*"]                            # nothing to package; tests are the artifact

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-m 'not live'"                       # unit-fake by default; opt-in with `-m live`
markers = [
    "live: opt-in tests that require a real Kubernetes cluster (see tests/README.md)",
    "loadtest: reserved for Group 8 ClusterLoader2 wrappers in a follow-up PR",
]
```

Install path for the implementer:

```bash
cd examples/openclaw-gvisor-sandbox
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

### 4.2 `tests/conftest.py` — fakes and fixtures

**Six fake classes.** Every fake:
- Is hand-written Python (no library shortcuts other than `MagicMock` for wide sub-objects).
- Uses real `threading.Lock` where concurrent access is possible.
- Records its interaction history so tests can assert on it.
- Exposes an `assert_no_leaks()` method for the autouse teardown.

Give each a `__repr__` for pytest failure output.

#### 4.2.1 `FakeSandbox`

Attributes: `name`, `namespace`, `spec_operating_mode` (default `"Running"`), `status_conditions` (list of `{type, status, reason, message, lastTransitionTime}`), `annotations` (dict).

Methods:
- `set_operating_mode(mode: str)` — `"Running"` or `"Suspended"`; updates state, records timestamp.
- `set_condition(type: str, status: str, reason: str = "", message: str = "")`.
- `set_annotation(key: str, value: str)`.
- `to_object() -> dict` — returns a dict shaped like a real `Sandbox` CR (for K8s-facing tests).
- `assert_no_leaks()` — no-op for now (state cleanup happens at test scope).

#### 4.2.2 `FakeOpenClaw`

An in-process `aiohttp.web.Application` bound to `127.0.0.1:0` (kernel-assigned port). Started/stopped by a fixture (see below).

Endpoints:
- `GET /` — returns `200 OK` after `startup_delay_seconds` (default 0; scriptable for the responsiveness test).
- `GET /v1/health/idle` — returns `{"idle": bool, "pendingCount": int, "details": {"queueSize": ..., "pendingReplies": ..., "activeEmbeddedRuns": ..., "activeTasks": ...}}`. `pendingCount = queueSize + pendingReplies + activeEmbeddedRuns + activeTasks`.
- `GET /v1/cron/next` — returns `{"nextRunTime": ISO8601 string | null}`.
- `POST /api/v1/chat` — receives a chat message, forwards to the linked `FakeLLM`, returns the response envelope.
- `GET /api/v1/lifecycle/status` — returns the same shape as `/v1/health/idle`.

Attributes:
- `queue_size`, `pending_replies`, `active_embedded_runs`, `active_tasks` — settable counters; the idle endpoint reads these.
- `next_run_time: str | None`.
- `base_url` — set after startup, e.g. `http://127.0.0.1:34211`.
- `linked_llm: FakeLLM | None`.

Methods: `set_counter(name, value)`, `assert_no_leaks()` → asserts `pendingCount == 0`.

#### 4.2.3 `FakeLifecycleDaemon`

Two modes, one class.

**Unit mode** (default): in-memory patch log; no cluster interaction.
- Endpoints (aiohttp on `127.0.0.1:0`):
  - `POST /v1/sandbox/suspend` — records `{"name", "namespace", "op": "suspend"}` in `patch_log`.
  - `POST /v1/sandbox/resume` — records `{"name", "namespace", "op": "resume"}`.
  - `GET /v1/sandbox/status?name=&namespace=` — returns a scripted state.
- Attributes: `patch_log: list[dict]`, `scripted_status: dict`.

**Live mode** (constructed with `k8s_client=<kubernetes.client.CustomObjectsApi>`): the same endpoints, but on `/v1/sandbox/resume` the handler actually issues:
```python
k8s_client.patch_namespaced_custom_object(
    group="agents.x-k8s.io", version="v1beta1",
    plural="sandboxes", namespace=ns, name=name,
    body={"spec": {"operatingMode": "Running"}}
)
```
This is the ~30-line Option (a) stand-in for the real Lifecycle Daemon.

Methods: `assert_no_leaks()` → no-op (patch log is legitimate state to inspect).

#### 4.2.4 `FakeLLM`

An in-process `aiohttp.web.Application` speaking the OpenAI Chat Completions wire protocol.

Endpoints:
- `POST /v1/chat/completions` — receives an OpenAI-style request, records the full prompt (system + messages) into `prompts_received`, returns the next scripted response from `response_queue` (or a default `"ok"` response if queue is empty).

Attributes:
- `prompts_received: list[dict]` — full request bodies.
- `response_queue: list[dict]` — scripted responses tests push in.
- `base_url: str` — set after startup.

Methods: `push_response(text: str, **kwargs)`, `last_prompt() -> dict`, `assert_no_leaks()` → no-op.

#### 4.2.5 `FakeSandboxRouter`

In-process buffer-and-replay for WebSocket handshakes. Not a network server — a Python class that mimics the router's wake-on-traffic behavior for unit tests.

Methods:
- `receive_ws_handshake(sandbox_name: str) -> WSHandle` — if sandbox is `"Suspended"`, buffer the handshake in `buffered` and trigger `on_wake_needed(sandbox_name)`. If `"Running"`, forward immediately.
- `mark_ready(sandbox_name: str)` — triggered when the daemon reports Ready; replays any buffered handshakes.
- Attributes: `buffered: list[dict]`, `on_wake_needed: Callable[[str], None]` (injected by tests).

`assert_no_leaks()` → asserts `buffered == []`.

#### 4.2.6 `FakeK8s`

Thin wrapper around `kubernetes.client`'s built-in fake — this is the ONLY fake that reuses production code.

```python
from kubernetes.client import ApiClient, CustomObjectsApi

class FakeK8s:
    def __init__(self):
        self.api_client = ApiClient()          # in-memory; no network
        self.custom = CustomObjectsApi(self.api_client)
        self._objects: dict[tuple, dict] = {}   # (group, plural, ns, name) -> object dict

    def apply(self, obj: dict) -> None: ...
    def get(self, group: str, plural: str, ns: str, name: str) -> dict: ...
    def patch(self, group: str, plural: str, ns: str, name: str, patch: dict) -> dict: ...
    def delete(self, group: str, plural: str, ns: str, name: str) -> None: ...
    def assert_no_leaks(self) -> None: ...      # optional: assert no dangling objects
```

#### 4.2.7 Autouse fixtures

```python
_ACTIVE_FAKES: list = []

@pytest.fixture
async def fake_openclaw():
    app = FakeOpenClaw()
    runner = aiohttp.web.AppRunner(app.aiohttp_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)   # kernel-assigned port
    await site.start()
    app.base_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    _ACTIVE_FAKES.append(app)
    yield app
    await runner.cleanup()

# Repeat for fake_llm, fake_daemon (unit mode). fake_router is pure Python
# (no aiohttp) so its fixture is synchronous.

@pytest.fixture(autouse=True)
def _leak_check():
    yield
    for f in _ACTIVE_FAKES:
        f.assert_no_leaks()
    _ACTIVE_FAKES.clear()
```

Rationale for kernel-assigned `:0`: race-free across parallel workers, no `unused_tcp_port_factory` dependency, and the assigned port is captured back into the fake so tests just read `fake.base_url`.

Also register a session fixture `kube_client` that loads `KUBECONFIG` on first use, used by live tests.

### 4.3 `tests/_helpers.py`

Small utility module. Prefix filename with `_` so pytest doesn't try to collect it as tests.

Functions to include:
- `wait_until(predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 0.5, message: str = "") -> None` — poll a predicate, `AssertionError` on timeout.
- `kubectl_exec(pod: str, cmd: list[str], namespace: str = "default", *, input: str | None = None) -> str` — thin `subprocess.run` wrapper.
- `kubectl_apply(path: str, namespace: str = "default") -> None`.
- `kubectl_delete(path: str, namespace: str = "default", ignore_missing: bool = True) -> None`.
- `canary_write(pod: str, path: str, value: str)` — kubectl-exec-based, matches the pattern in `run-test-kind.sh` lines 129-132.
- `canary_read(pod: str, path: str) -> str`.
- `require_paired_json(pod: str) -> None` — checks whether `/workspace/.openclaw/devices/paired.json` exists in the target pod; calls `pytest.skip(...)` with a message pointing to § 1a.A if absent. (When § 1a.A is resolved, replace this with the actual seeding function.)
- `require_fakellm_config(pod: str) -> None` — checks whether the pod's `/etc/openclaw/openclaw.json` contains a `models.providers.fakellm` block; skips with a reason pointing to § 1a.B if not. (When § 1a.B is resolved, replace with an actual override-application function.)
- `require_fakellm_reachable_from_pod(pod: str, base_url: str) -> None` — `kubectl exec`s a one-shot `curl` inside the pod against `base_url`; skips with a reason pointing to § 1a.C if unreachable. (When § 1a.C is resolved, this becomes a no-op or is removed.)
- `nodeport_url(port: int = 30789) -> str` — returns the URL for reaching the OpenClaw gateway via its NodePort. `kind` returns `http://127.0.0.1:30789` (matches [`kind-service.yaml`](kind-service.yaml)); on GKE the tester is expected to have an IAP tunnel mapping `localhost:18789` to the NodePort (see `README.md` § "On GKE"), and the helper reads `OPENCLAW_TEST_GATEWAY_URL` env var if set to override. Live tests that hit the real gateway MUST use this helper — never `kubectl port-forward`, which is broken under gVisor.
- `fake_llm_provider_block(base_url: str, model_id: str = "fake-claude") -> dict` — returns the `openclaw.json` provider block matching `TESTS_PROPOSAL.md` Assumption 5.

### 4.4 Test files

For each test file below: create it in `tests/`, use plain pytest functions (no classes, matching PR #1049 style). Add a docstring at the top of every file describing what group it covers and any special prereqs.

#### `test_bootstrap.py` — Group 7 (2 unit contract tests + 3 live behavior tests)

**Unit (contract) tests — assert on wire shapes we believe OpenClaw uses; do not launch OpenClaw:**

- `test_gateway_root_responds_within_deadline` — hit `fake_openclaw.base_url + "/"`, assert HTTP 200 and elapsed < **1500 ms**. Verifies the deadline logic our fixtures apply, not OpenClaw's real speed.
- `test_openclaw_gateway_token_env_var_honored` — configure `FakeOpenClaw` with token, assert unauthenticated `GET` returns 401 and `Authorization: Bearer xyz` returns 200. Verifies our fake's token-check contract matches what we expect OpenClaw to do; this test **does not** apply `--auth none`.

**Live (behavior) tests — `@pytest.mark.live`, launch real OpenClaw pod:**

- `test_gateway_root_responds_within_deadline_live` — hit `nodeport_url() + "/"` against the real pod, same 1500 ms threshold. Guards: `require_paired_json` (§ 1a.A) — no config or FakeLLM needed for a root GET.
- `test_openclaw_provider_api_key_detected_at_startup_live` — apply example manifests with the (yet-to-be-decided) `openclaw.json` override that adds a `fakellm` provider block pointed at a session-scoped `FakeLLM`. Assert the pod starts and its `/v1/health/idle` reports provider ready. Guards: `require_paired_json` (§ 1a.A), `require_fakellm_config` (§ 1a.B), `require_fakellm_reachable_from_pod` (§ 1a.C) — auto-skips until all three open questions are resolved.
- `test_hello_world_chat_turn_via_fakellm_live` — with the same fixture, send one `POST /api/v1/chat` via `nodeport_url()`, assert `fake_llm.last_prompt()` shape and response envelope. Same three guards as above.

#### `test_memory.py` — Group 1 (2 unit contract tests + 5 live behavior tests)

**Unit (contract) tests — pure Python, no OpenClaw:**

- `test_markdown_memory_frontmatter_and_sections_parseable` — write a memory `.md` file with the frontmatter schema we believe OpenClaw uses, parse it with `_helpers.parse_qmd_frontmatter(...)`, assert schema. Verifies our parser matches the format documented at `src/memory/qmd-manager.ts`.
- `test_memory_content_byte_identical_across_suspend_cycle` — pure file-hash test. Populate a `tmp_path`-rooted memory directory with SQLite + markdown files, hash contents, simulate the suspend/resume cycle as a no-op (files untouched), hash again, assert equal. Verifies our hashing helper.

**Live (behavior) tests — `@pytest.mark.live`, launch real OpenClaw pod pointed at `FakeLLM`. All guard with `require_paired_json` (§ 1a.A), `require_fakellm_config` (§ 1a.B), `require_fakellm_reachable_from_pod` (§ 1a.C); auto-skip until all three are resolved:**

- `test_short_term_memory_survives_within_single_session_live` — send turn A ("my name is Alice"), send turn B ("what's my name?"), assert `fake_llm.last_prompt()` contains "Alice".
- `test_short_term_memory_lost_after_pvc_suspend_live` — send A, patch `operatingMode=Suspended`, wait pod gone, patch back to `Running`, wait ready, send B, assert prompt does NOT contain A's content.
- `test_long_term_memory_written_to_disk_on_remember_intent_live` — send "please remember X", `kubectl exec` into the pod to verify a file appears under `/workspace/.openclaw/memory/<agentId>.sqlite` with X.
- `test_long_term_memory_survives_pvc_suspend_live` — write LTM, suspend/resume cycle, send query, assert `fake_llm.last_prompt()` includes the LTM content.
- `test_relevant_ltm_entries_injected_into_prompt_live` — seed 3 LTM entries via chat, ask about entry #2, assert `fake_llm.last_prompt()` includes entry #2's content and (optionally) not the others.

Detailed memory paths and formats: `TESTS_PROPOSAL.md` § Assumption 6 and the memory file `openclaw-test-suite-scope` § "OpenClaw facts."

#### `test_pvc_preservation.py` — Group 2 (2 live tests)

Note: the previously-planned `test_operating_mode_mirrored_from_sandboxclaim_to_sandbox` was removed. It tests `SandboxClaim.spec.operatingMode` mirroring — a field that doesn't exist on main and belongs to plan PR 1. When plan PR 1 lands, its own PR should ship the mirroring test. The direct-`Sandbox` operatingMode round-trip below already gives us end-to-end coverage of what works today (verified in the controller at `controllers/sandbox_controller.go:728-757` and `:1093-1166`).

- `test_pvc_survives_pod_delete_and_respawn` (`@pytest.mark.live`)
  Port `run-test-kind.sh` lines 127-165 into Python using `_helpers.canary_write` / `canary_read`. Apply example manifests via `kubectl apply -f openclaw-config.yaml -f openclaw-template.yaml -f openclaw-warmpool.yaml -f openclaw-claim.yaml` in a session fixture; clean up in teardown.
- `test_pvc_survives_operating_mode_suspend_then_resume` (`@pytest.mark.live`)
  Write canary; `kubectl patch sandbox <name> --type=merge -p '{"spec":{"operatingMode":"Suspended"}}'`; `wait_until` pod is gone; patch back to `Running`; `wait_until` pod is Ready; read canary; assert equal.

#### `test_snapshot_preservation.py` — Group 3 (5 tests, all live)

At the top of the file:
```python
pytestmark = pytest.mark.live

@pytest.fixture(scope="session", autouse=True)
def _require_pod_snapshot_crds():
    """Skip every test in this module if the Pod Snapshot Controller isn't installed."""
    # PodSnapshotSandboxClient.__init__ validates the required CRDs are present
    # on the cluster (see podsnapshot_client.py:50-51). Delegate to it — kills
    # the need to guess CRD plurals in this fixture.
    try:
        from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient
        PodSnapshotSandboxClient()
    except Exception as e:
        pytest.skip(f"Pod Snapshot Controller not available: {e}")
```
CRD constants for reference (from `clients/python/agentic-sandbox-client/k8s_agent_sandbox/constants.py:33-36`): group `podsnapshot.gke.io`, version `v1`, plurals `podsnapshots` / `podsnapshotmanualtriggers`. We do NOT hard-code these in the fixture; the client does the check.

All five tests use `PodSnapshotSandboxClient`:

```python
from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient
```

- `test_snapshot_preserves_active_shell_sleep_process`
- `test_snapshot_preserves_node_event_loop_timer`
- `test_snapshot_preserves_open_websocket`
- `test_pod_restored_condition_becomes_true_after_resume`
- `test_restore_from_specific_snapshot_uid`

Full per-test descriptions: `TESTS_PROPOSAL.md` § "test_snapshot_preservation.py — Group 3 (Snapshot / CRIU) — LIVE".

Prereqs (documented in `tests/README.md`): GKE ≥ 1.35.2-gke.1842000 + gVisor + Pod Snapshot Controller + GCS bucket + `PodSnapshotPolicy` with `agents.x-k8s.io/sandbox-name-hash` grouping label.

#### `test_idleness.py` — Group 5 (4 tests, all unit-fake)

- `test_idle_endpoint_reports_true_on_fresh_boot` — new `FakeOpenClaw`, `GET /v1/health/idle`, assert `{"idle": true, "pendingCount": 0}`.
- `test_idle_endpoint_pendingcount_matches_component_sum` — parametrize over multiple (q, r, e, t) tuples, assert `pendingCount == sum`.
- `test_lifecycle_daemon_calls_suspend_after_max_idle_time` — configure daemon with `max_idle_seconds=1`, keep `fake_openclaw.pending_count == 0` for 2 seconds, assert `fake_daemon.patch_log` contains one `suspend` entry.
- `test_lifecycle_daemon_does_not_suspend_while_pending` — same setup, keep `pending_count > 0`, assert `patch_log == []` after 2 seconds.

#### `test_connections.py` — Group 6 (5 tests: 3 unit + 2 live)

- `test_concurrent_ws_connections_track_pending_arithmetically` (unit) — open 3 WS to `FakeOpenClaw`, assert `pendingCount == 3`; close 2, assert `pendingCount == 1`.
- `test_ws_disconnect_returns_endpoint_to_idle_after_grace` (unit).
- `test_wake_on_traffic_buffers_and_replays_ws_handshake` (unit) — `FakeSandbox.set_operating_mode("Suspended")`, call `fake_router.receive_ws_handshake(sandbox.name)`, assert buffered, call `fake_router.mark_ready(sandbox.name)`, assert replayed.
- `test_wake_on_traffic_via_real_sandbox_router` (`@pytest.mark.live`) — deploy real `sandbox-router/` (in the test cluster) in front of a suspended OpenClaw sandbox. Boot `FakeLifecycleDaemon(k8s_client=kube_client.custom)` (Option a live mode). Open a WS. Assert: router buffers, calls daemon's `/v1/sandbox/resume`, daemon patches operating mode to Running, pod becomes Ready, handshake replays. Convention reference: `sandbox-router/dev/smoke-test/run.sh` (kind + kubectl + real HTTP).
- `test_ws_closed_client_side_on_pvc_mode_suspend` (`@pytest.mark.live`) — connect WS to running pod, patch `operatingMode=Suspended`, `wait_until` client observes close, assert.

#### `test_cron_gateway.py` — Group 4 (skip-stubs only)

Two skipped test bodies, `@pytest.mark.skip(reason=..., strict=True)`. Reasons copied verbatim from `TESTS_PROPOSAL.md` § "Skip-stub files (this round)":

```python
@pytest.mark.skip(
    strict=True,
    reason=(
        "OpenClaw v2026.3.23 does not expose /v1/cron/next; endpoint is planned "
        "per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/"
        "plan/openclaw_idle_and_wake.md#2-inner-cron-integration--dynamic-pre-wakeup-jobs. "
        "Un-skip once the OpenClaw image with this endpoint ships."
    ),
)
def test_cron_next_returns_iso8601_timestamp_of_next_scheduled_run():
    ...  # skeleton bodies for the day the endpoint lands

@pytest.mark.skip(
    strict=True,
    reason=(
        "External Postgres/Spanner backend for cron/memory tables is a future "
        "architecture per https://github.com/tomergee/agent-sandbox/blob/"
        "openclaw-integration/plan/massive_scaling_openclaws.md. Un-skip once "
        "OpenClaw supports pointing SQLite-backed stores at an external DB."
    ),
)
def test_external_db_backend_reads_cron_from_postgres_not_local_sqlite():
    ...
```

### 4.5 `tests/loadtest/README.md`

Stub for the next PR. Should include:
- One paragraph explaining Group 8 (density + throughput) is deferred to a follow-up PR.
- The two planned recipe filenames: `openclaw-density-test.yaml` and `openclaw-throughput-test.yaml`.
- The parameter tables from `TESTS_PROPOSAL.md` § "Density & performance — Group 8" (density: `NUM_SANDBOXES`, `TUNING_SET`, `WARMPOOL_REPLICAS`; throughput: `TARGET_QPS`, `DURATION_MINUTES`, `POOL_SIZE`).
- A link to the reference convention: [`dev/load-test/README.md`](../../../../dev/load-test/README.md).

### 4.6 `tests/README.md`

Documents how to run the suite. Should include:

**Setup:**
```bash
cd examples/openclaw-gvisor-sandbox
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

**Unit tests (default; no cluster needed):**
```bash
pytest                     # runs everything NOT marked @pytest.mark.live
```

**Live tests (require a cluster):**
```bash
export KUBECONFIG=~/.kube/config              # or your cluster's config
pytest -m live                                 # runs only live tests
pytest -m "live or not live"                   # runs everything, live + unit
```

**Live test cluster requirements:**
- Group 2 (PVC) + Group 6 (Connections wake-on-traffic) live tests: any kind cluster with gVisor + the example manifests applied.
- Group 3 (Snapshot) live tests: GKE standard cluster ≥ 1.35.2-gke.1842000 + gVisor + Pod Snapshot Controller + GCS bucket + `PodSnapshotStorageConfig` + `PodSnapshotPolicy` (with `agents.x-k8s.io/sandbox-name-hash` grouping label). See [snapshots README](../../../clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots/README.md).
- Group 7 live bootstrap + Group 1 live memory tests: additionally require the three items in plan.md § 1a (paired.json seeded, `openclaw.json` FakeLLM override applied, FakeLLM reachable from pod). Until those are resolved, these tests auto-skip.

**Gateway ingress convention:** live tests reach the OpenClaw gateway via **NodePort 30789** on kind (per [`kind-service.yaml`](../kind-service.yaml)), matching the existing `run-test-kind.sh`. On GKE, open an IAP tunnel to the NodePort per the `## On GKE` section of [`README.md`](../README.md) and set `OPENCLAW_TEST_GATEWAY_URL=http://localhost:18789`. **Do not use `kubectl port-forward`** — it is broken under gVisor.

**Running a single group:**
```bash
pytest tests/test_memory.py            # just memory tests
pytest tests/test_pvc_preservation.py -m live   # just live PVC tests
```

---

## 5. Commit sequence (four commits, one PR)

Commit granularity chosen for review readability, not enforced by tooling.

### Commit 1 — Scaffolding, fakes, helpers, docs stubs

**Adds:**
- `pyproject.toml`
- `tests/conftest.py` (all six fakes + autouse `_leak_check`)
- `tests/_helpers.py`
- `tests/README.md`
- `tests/loadtest/README.md`

**Touches:**
- `README.md` (add Tests section pointer)
- `run-test-kind.sh` (add one-line comment)

**Acceptance:**
1. `pip install -e ".[test]"` succeeds.
2. `python -c "import tests.conftest"` succeeds (imports resolve without ImportError — aiohttp, kubernetes, k8s_agent_sandbox all installed and the fake classes construct without missing dependencies).
3. `pytest --collect-only` shows zero tests (no test files yet) and zero collection errors.

### Commit 2 — Unit-fake suites (bootstrap contract, memory contract, idleness, cron skip-stubs) + bootstrap/memory live stubs

**Adds:**
- `tests/test_bootstrap.py` (2 unit contract tests + 3 live behavior tests, live-marked)
- `tests/test_memory.py` (2 unit contract tests + 5 live behavior tests, live-marked)
- `tests/test_idleness.py` (4 unit tests)
- `tests/test_cron_gateway.py` (2 skipped)

**Acceptance:** `pytest -q` (unit-only, default `-m 'not live'`) reports **8 passed, 2 skipped, 8 deselected, 0 failed**. Runtime < 10 seconds.

### Commit 3 — PVC and unit-fake connections

**Adds:**
- `tests/test_pvc_preservation.py` (2 live tests marked `@pytest.mark.live`; no unit tests in this group)
- Unit portion of `tests/test_connections.py` (3 unit tests marked as such)

**Acceptance:** `pytest -q` reports **11 passed, 2 skipped, 10 deselected, 0 failed** (2 skipped = the two Group 4 cron stubs; deselected = connections live + PVC live). `pytest -q -m live tests/test_pvc_preservation.py` against a real kind cluster passes 2 tests.

### Commit 4 — Live wake-on-traffic + snapshot

**Adds:**
- Live portion of `tests/test_connections.py` (2 live tests)
- `tests/test_snapshot_preservation.py` (5 live tests + session skip fixture)

**Acceptance:** `pytest -q` still reports **11 passed, 2 skipped, 17 deselected, 0 failed**. On a kind cluster with `paired.json` resolved: `pytest -q -m live tests/test_connections.py` passes; `pytest -q -m live tests/test_bootstrap.py` passes 3. On a GKE cluster with Pod Snapshot Controller: `pytest -q -m live tests/test_snapshot_preservation.py` passes 5. Without paired.json seeded, live bootstrap/memory tests auto-skip. Without Pod Snapshot CRDs, all 5 snapshot tests auto-skip.

---

## 6. Explicit assumptions to call out in the PR description

Flag these in the PR body so the reviewer can push back if needed:

1. **Three test-infra ↔ pod bridging questions are deferred (see § 1a).** Live tests auto-skip via `require_paired_json` (§ 1a.A), `require_fakellm_config` (§ 1a.B), and `require_fakellm_reachable_from_pod` (§ 1a.C) until each is resolved. Reaching the real gateway from tests is settled: **NodePort 30789** via `nodeport_url()`; `kubectl port-forward` is broken under gVisor and must not be used.
2. **`KUBECONFIG` convention** for live-test cluster selection — standard env var, no custom pytest option.
3. **Live tests reuse the example's existing `openclaw-*.yaml` manifests** — applied in a session fixture, torn down in teardown. Manifests are not duplicated inside `tests/`.
4. **Wake-on-traffic uses a ~30-line test-side HTTP handler** as stand-in for the missing Lifecycle Daemon (Option a from proposal Q10). Same `FakeLifecycleDaemon` class; live mode of that class patches real K8s. Replaced entirely by the real daemon when plan PR 2 lands.
5. **Group 3 auto-skips on kind** — session fixture inspects the cluster for Pod Snapshot CRDs and skips (not fails) when absent.
6. **Timing headroom** — every timing assertion (`test_gateway_root_responds_within_deadline`, idle-transition tests) uses 3-10× headroom over expected values to avoid CI flakes. Documented as comments in the tests themselves.
7. **Template runs non-root as of commit `4872f65`** (`runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`, `runAsNonRoot: true`, `capabilities.drop: [ALL]` on both init and main containers; `HOME=/workspace`; PVC mounted at `/workspace/.openclaw` instead of `/root/.openclaw`). Test-side `kubectl exec` commands run as uid 1000 by default and PVC writes work via fsGroup=1000. Tests do not assume root and do not require the container to be started with elevated privileges. sshd (formerly on port 18790) was also removed by that commit — the port declaration is stale in the shipped template but not exercised by any test.

---

## 7. Out of scope for this PR (deferred to future PRs)

- Group 8 (ClusterLoader2 recipes) — ships only a README stub. Full recipes + drivers under `tests/loadtest/` in the follow-up PR.
- Live-harness `scenarios.py` with markdown report artifact — separate PR.
- Full Group 4 coverage — blocked on OpenClaw exposing `/v1/cron/next`.
- Live-side idleness tests — blocked on OpenClaw exposing `/v1/health/idle`.
- E2E port of pytest scenarios into a Go harness — stage 2.
- External-DB tests — blocked on OpenClaw supporting external Postgres/Spanner backends.
- CI wiring — no repo-wide kind lane exists today; adding one is a separate infra PR.

---

## 8. Common gotchas / cheat-sheet

- **"pytest collects 0 tests":** you're in the wrong directory. Run from `examples/openclaw-gvisor-sandbox/`.
- **"live tests aren't running":** `addopts = "-m 'not live'"` excludes them by default. Use `pytest -m live` or `pytest -m "live or not live"`.
- **"aiohttp fake never starts":** check that the fixture is `async def` and `asyncio_mode = "auto"` is set in `pyproject.toml`.
- **"leak-check keeps failing":** a test opened a WS or spawned a task and didn't clean up. Add cleanup in a `try/finally` inside the test, or as a per-test fixture. Don't disable the autouse check.
- **"kubectl not found in live tests":** live tests shell out; make sure `kubectl` is in `PATH` for the pytest process.
- **"snapshot tests all fail with `RuntimeError`":** the session-scope `_require_pod_snapshot_crds` fixture didn't catch the absent CRDs correctly. Verify with `kubectl get crds | grep podsnapshot` first.
- **"live bootstrap/memory tests all skip":** one of the § 1a guards fired. Check the skip reason for which subsection (A, B, or C) blocked it. Until § 1a is decided, either seed the missing state manually or accept that these tests won't run end-to-end.
- **"live test hangs trying to reach the gateway":** you probably used `kubectl port-forward`. Broken under gVisor. Use `nodeport_url()` from `_helpers.py` — hits `http://127.0.0.1:30789` on kind (per [`kind-service.yaml`](kind-service.yaml)) or your IAP-tunneled port on GKE.
- **"pod can't reach FakeLLM":** § 1a.C in action. `FakeLLM`'s `127.0.0.1` is the test host, not the pod's loopback. On kind + Docker Desktop use `host.docker.internal`; on Linux kind the docker bridge gateway; on GKE this doesn't work at all without a tunnel. `require_fakellm_reachable_from_pod` checks this and skips clearly.
- **"port conflicts in parallel test runs":** every fake binds `127.0.0.1:0` (kernel-assigned); if you see a fixed-port collision, someone hardcoded a port instead of letting the kernel pick and capturing it back into `fake.base_url`.
- **"permission denied writing to /workspace/.openclaw/…":** you assumed uid 0. Container runs as uid 1000 (commit `4872f65`). PVC allows uid 1000 writes via fsGroup=1000, but the target directory may need `mkdir -p` first — `kubectl exec POD -- mkdir -p /workspace/.openclaw/devices` before the write.

---

## 9. External references (open only if you need them)

- **Spec:** [`TESTS_PROPOSAL.md`](TESTS_PROPOSAL.md) — every test's rationale + all decisions with links.
- **Style reference:** [`examples/agent-sandbox-rl/tests/`](../../examples/agent-sandbox-rl/tests) — read `conftest.py` (fake pattern) and `test_fleet.py` (test naming/assertions).
- **Sandbox controller (verified: honors operatingMode):** [`controllers/sandbox_controller.go`](../../controllers/sandbox_controller.go) — suspend path at lines 728-757, PVC reconciliation at lines 1093-1166.
- **Snapshot extension:** [`clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots/`](../../clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots) — `PodSnapshotSandboxClient` usage in the README, integration test at `test_podsnapshot_extension.py`.
- **Sandbox router (live wake-on-traffic pattern):** [`sandbox-router/dev/smoke-test/run.sh`](../../sandbox-router/dev/smoke-test/run.sh) — how the router is exercised against a real cluster.
- **Load-test convention (for follow-up PR):** [`dev/load-test/README.md`](../../dev/load-test/README.md).

---

## 10. Definition of done

- Four commits pushed to the PR branch, matching § 5.
- `pytest` from `examples/openclaw-gvisor-sandbox/` returns green: **11 passed, 2 skipped, 17 deselected, 0 failed**. Runtime under 15 seconds.
- Live tests independently verified against a real cluster (evidence in PR description: paste `pytest -m live` output for Groups 2 and 6 on kind; note Group 3 status per cluster capability; live bootstrap/memory results dependent on § 1a resolution).
- PR description names all assumptions from § 6, and explicitly flags § 1a as an open item to resolve in a follow-up before the live bootstrap/memory tests can pass end-to-end.
- `TESTS_PROPOSAL.md` and this `plan.md` remain in the tree until the PR is reviewed.