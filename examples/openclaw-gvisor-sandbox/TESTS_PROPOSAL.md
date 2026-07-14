# OpenClaw Sandbox — Test Suite Proposal (draft)

**Status:** proposal for review — not yet implemented.
**Location:** `examples/openclaw-gvisor-sandbox/tests/`
**Reference:** modeled on [kubernetes-sigs/agent-sandbox#1049](https://github.com/kubernetes-sigs/agent-sandbox/pull/1049) (`agent-sandbox-rl` performance & scale PR, merged 2026-07-07 — 188 mocked pytest tests + a live load-test harness) — same fake-first discipline, leak-check invariants, and separation of unit tests from a live harness.

## Framework

Two tracks, run separately.

**Track 1 — Correctness suite: Python + pytest.** `pyproject.toml` at the example root (mirrors [`examples/agent-sandbox-rl/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/agent-sandbox-rl) — top-level `pyproject.toml`, `tests/` subdirectory, no source package).
- **Fake-first for unit tests**: no cluster required, run in seconds on every push.
- **Live tests via `@pytest.mark.live`**: opt-in (`pytest -m live`); require a real cluster. Used for PVC round-trips, snapshot lifecycle, and wake-on-traffic through the real router.
- **Snapshot lifecycle uses the existing client extension** [`clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots) — `PodSnapshotSandboxClient` and `SandboxWithSnapshotSupport` give us `.snapshots.create(...)`, `.suspend(snapshot_before_suspend=True)`, `.resume()`, `.restore(snapshot_uid=...)`. Reference integration test lives at [`clients/python/agentic-sandbox-client/test_podsnapshot_extension.py`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/clients/python/agentic-sandbox-client/test_podsnapshot_extension.py).
- **Wake-on-traffic tests use the real Go router** at [`sandbox-router/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/sandbox-router) (from [PR #838](https://github.com/kubernetes-sigs/agent-sandbox/pull/838), merged 2026-07-07). The cluster-level testing convention we follow is [`sandbox-router/dev/smoke-test/run.sh`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/sandbox-router/dev/smoke-test/run.sh) — spins a kind cluster, deploys the router pod, drives real HTTP through it, and asserts on cache invalidation / routing behavior. (The router's in-process Go integration tests under [`sandbox-router/proxy/*_integration_test.go`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/sandbox-router/proxy) use `httptest.NewServer` and are the router's own unit-level integration tests, not cluster tests — different pattern.)

**Track 2 — Density & performance: ClusterLoader2.** Not pytest. YAML test recipes ship in this example's own `tests/loadtest/` subfolder — they exercise OpenClaw-specific workloads, so they belong with the example, not in the shared platform load-test directory. The reference for **how ClusterLoader2 is used in this repo** is [`dev/load-test/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test) — read [`dev/load-test/README.md`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/dev/load-test/README.md) and [`agent-sandbox-load-test.yaml`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/dev/load-test/agent-sandbox-load-test.yaml) for the invocation flow and recipe shape, then apply the same conventions here. Configurable flags for `numSandboxes`, tuning set, and OpenClaw-specific probe parameters. Emits `junit.xml`. Invoked exactly like the existing load test:

```bash
# From the sibling perf-tests/clusterloader2 checkout
./clusterloader2 \
  --testconfig=../../agent-sandbox/examples/openclaw-gvisor-sandbox/tests/loadtest/openclaw-density-test.yaml \
  --kubeconfig=$HOME/.kube/config \
  --provider=gke     # or --provider=kind
```

Or, matching the existing recipes' shell-driver convention, wrap it in a `run_openclaw_density.sh` alongside the recipe.

**E2E porting (stage 2, not this PR):** the pytest correctness suite is designed so scenarios can be lifted into an e2e harness later, following the [`dev/load-test/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test) style. The existing [`run-test-kind.sh`](run-test-kind.sh) continues to serve as the shell smoke path.

## Directory structure

```
examples/openclaw-gvisor-sandbox/
├── README.md                          (existing)
├── openclaw-*.yaml, kind-*.yaml, gke-service.yaml  (existing)
├── run-test-kind.sh                   (existing — shell smoke path)
├── pyproject.toml                     NEW — pytest config, deps, `live` marker
└── tests/                             NEW
    ├── conftest.py                    fakes + autouse leak-check fixtures
    ├── test_bootstrap.py              Group 7 — responsiveness, token, API key, hello world
    ├── test_memory.py                 Group 1
    ├── test_pvc_preservation.py       Group 2
    ├── test_snapshot_preservation.py  Group 3 — LIVE via PodSnapshotSandboxClient
    ├── test_idleness.py               Group 5
    ├── test_connections.py            Group 6 — includes live wake-on-traffic
    ├── test_cron_gateway.py           Group 4 — mixed live/skip-stubs
    └── loadtest/                      NEW subfolder — Group 8 ClusterLoader2 recipes
        ├── openclaw-density-test.yaml     NEW — N concurrent sandboxes, startup latency
        ├── openclaw-throughput-test.yaml  NEW — sustained QPS through the router
        ├── run_openclaw_density.sh        NEW — driver wrapper
        ├── run_openclaw_throughput.sh     NEW — driver wrapper
        └── templates/                     NEW — OpenClaw-specific sandbox templates for the recipes
```

Naming and driver-script conventions are inherited from [`dev/load-test/test-recipes/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test) — the six existing recipes there (`high-volume-test.yaml`, `rapid-burst-test.yaml`, `throughput-test.yaml`, `warmpool-burst-test.yaml`, `medium-scale-concurrent-load-test.yaml`, `sandbox-capacity-cliff-test.yaml`) all use the `<scenario>-test.yaml` suffix with companion `run_<scenario>.sh` driver scripts, and templates live in a nested `templates/` subdir. We mirror those conventions in our own `tests/loadtest/`.

## Shared fakes (`conftest.py`)

Hand-written, mirror the real interface, thread-safe where the code under test hits them concurrently. Same pattern as [`FakeCluster` in `examples/agent-sandbox-rl/tests/conftest.py`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/examples/agent-sandbox-rl/tests/conftest.py) — a hand-written class with a real `threading.Lock`, `MagicMock` reserved for wide K8s API interfaces, sensible default return values pre-wired.

- **`FakeSandbox`** — scriptable `spec.operatingMode` / `status.conditions` / annotations.
- **`FakeOpenClaw`** — in-memory implementation of the OpenClaw HTTP surface (`/v1/health/idle`, `/v1/cron/next`, `/api/v1/chat`, `/api/v1/lifecycle/status`). Four settable counters backing the idle formula.
- **`FakeLifecycleDaemon`** — implements `/v1/sandbox/{suspend,resume,status}`; records every patch it would issue.
- **`FakeLLM`** — HTTP server speaking OpenAI-completions wire protocol; records every prompt.
- **`FakeSandboxRouter`** — simulates the wake-on-traffic buffer-and-replay path.
- **`FakeK8s`** — thin wrapper around the Python K8s client's fake mode.

Every fake that owns background threads gets registered on creation and torn down in a `yield`-teardown block. Every scenario test asserts a no-leak invariant at the end (`FakeOpenClaw.pending_count == 0`, `FakeSandboxRouter.buffered == []`, etc.).

## Tests in scope for first PR

### `test_bootstrap.py` — Group 7 (Bootstrap / smoke) — NEW

Fast, mostly-unit-fake sanity tests that any regression would break. Runs first in every CI invocation.

- `test_gateway_root_responds_within_deadline` — HTTP `GET /` returns within N seconds (unit-fake asserts the handler exists; live variant asserts real pod responds).
- `test_openclaw_gateway_token_env_var_honored` — with `OPENCLAW_GATEWAY_TOKEN=xyz`, unauthenticated `GET` is rejected and `Authorization: Bearer xyz` succeeds. (When paired with `--auth none` for the rest of the suite, this test explicitly does NOT set that flag.)
- `test_openclaw_provider_api_key_detected_at_startup` — with a `FakeLLM` block in `openclaw.json`, startup reports the provider as ready via the same code path the real Anthropic/OpenAI providers use.
- `test_hello_world_chat_turn_via_fakellm` — end-to-end: seed `paired.json`, boot fake gateway pointed at FakeLLM, send one chat turn, assert FakeLLM received a well-formed prompt and the client got a valid response envelope.

### `test_memory.py` — Group 1 (Memory)

All unit-fake. FakeLLM records prompts so we can assert what the model actually saw.

- `test_short_term_memory_survives_within_single_session`
- `test_short_term_memory_lost_after_pvc_suspend`
- `test_long_term_memory_written_to_disk_on_remember_intent`
- `test_long_term_memory_survives_pvc_suspend`
- `test_markdown_memory_frontmatter_and_sections_parseable`
- `test_relevant_ltm_entries_injected_into_prompt`
- `test_memory_content_byte_identical_across_suspend_cycle`

### `test_pvc_preservation.py` — Group 2 (PVC)

Two live tests (`@pytest.mark.live`, opt-in). The direct `Sandbox` operatingMode round-trip below is what actually works on `main` today and covers our example's usage pattern. A separate `SandboxClaim.spec.operatingMode` mirroring test was previously listed here but has been removed — that field is planned for plan PR 1 and should ship with its own tests, not preemptively here.

- `test_pvc_survives_pod_delete_and_respawn` (live, ported from `run-test-kind.sh`)
- `test_pvc_survives_operating_mode_suspend_then_resume` (live)

### `test_idleness.py` — Group 5 (Idleness, unit-fake only)

- `test_idle_endpoint_reports_true_on_fresh_boot`
- `test_idle_endpoint_pendingcount_matches_component_sum`
- `test_lifecycle_daemon_calls_suspend_after_max_idle_time`
- `test_lifecycle_daemon_does_not_suspend_while_pending`

### `test_connections.py` — Group 6 (Connections)

Unit-fake tests + one live test against the real `sandbox-router/` deployment.

- `test_concurrent_ws_connections_track_pending_arithmetically` (unit)
- `test_ws_disconnect_returns_endpoint_to_idle_after_grace` (unit)
- `test_wake_on_traffic_buffers_and_replays_ws_handshake` (unit, against `FakeSandboxRouter`)
- `test_wake_on_traffic_via_real_sandbox_router` (live, `@pytest.mark.live`) — deploy the real [`sandbox-router/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/sandbox-router) in front of a suspended OpenClaw sandbox; open WS; assert the router buffers, triggers resume via the daemon (or its fake stand-in for now — see Q10), and replays the handshake once `Ready=True`. Follows the cluster-testing convention of [`sandbox-router/dev/smoke-test/run.sh`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/sandbox-router/dev/smoke-test/run.sh) — kind cluster, real router pod, real HTTP.
- `test_ws_closed_client_side_on_pvc_mode_suspend` (live)

### `test_snapshot_preservation.py` — Group 3 (Snapshot / CRIU) — LIVE

**Change from earlier draft: no longer all-skip.** GKE Pod Snapshot support already ships in the repo via [`PodSnapshotSandboxClient`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots). All tests use it directly:

```python
from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient

client = PodSnapshotSandboxClient()
sandbox = client.create_sandbox(warmpool="openclaw-pool", namespace="ns")
sandbox.snapshots.create("pre-suspend")
sandbox.suspend(snapshot_before_suspend=True)   # sets operatingMode=Suspended
sandbox.resume()                                # restores from latest snapshot
```

Tests (all `@pytest.mark.live`, prereq: GKE ≥ 1.35.2-gke.1842000 + gVisor + Pod Snapshot Controller + GCS bucket + `PodSnapshotPolicy` with the `agents.x-k8s.io/sandbox-name-hash` grouping label):

- `test_snapshot_preserves_active_shell_sleep_process` — start `sleep 300`, snapshot, resume, verify PID + remaining sleep time reduced only by wall-clock elapsed.
- `test_snapshot_preserves_node_event_loop_timer` — schedule `setTimeout(cb, 60_000)` in OpenClaw at T=0, snapshot at T=5s, resume at T=10s, assert callback fires at T≈65s (not T≈70s).
- `test_snapshot_preserves_open_websocket` — client holds WS, snapshot, resume, assert client did not observe a close event.
- `test_pod_restored_condition_becomes_true_after_resume` — after `.resume()`, poll `Sandbox.status.conditions[type=PodRestored]`, assert transitions to `status=True` within deadline.
- `test_restore_from_specific_snapshot_uid` — take two snapshots, suspend, `.restore(snapshot_uid=<older>)`, verify state matches older snapshot (not the newer one).

### Density & performance — Group 8 (ClusterLoader2, `tests/loadtest/`) — NEW

Not pytest — YAML recipes invoked via ClusterLoader2. **Reference** for how ClusterLoader2 is wired into this repo: [`dev/load-test/README.md`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/dev/load-test/README.md). Our recipes live in this example's own `tests/loadtest/` because they exercise OpenClaw-specific workloads, and mirror the naming / driver-script layout from `dev/load-test/test-recipes/`.

- **`openclaw-density-test.yaml`** (+ `run_openclaw_density.sh` driver) — creates `N` OpenClaw sandboxes concurrently (configurable via `--testoverrides` flag), measures startup latency, and waits for the gateway HTTP root to respond on each. Parameters:
  - `NUM_SANDBOXES` (default `10`, override with `--testoverrides=numSandboxes=100`)
  - `TUNING_SET` (default `Sequence`, alternatives `RandomizedLoad`, `Uniform5qps`)
  - `WARMPOOL_REPLICAS` (default `2`)
- **`openclaw-throughput-test.yaml`** (+ `run_openclaw_throughput.sh` driver) — sustained throughput: fixed pool of sandboxes, hits the chat endpoint at a target QPS through the router, measures p50/p95/p99 latency for `M` minutes. Parameters:
  - `TARGET_QPS` (default `10`)
  - `DURATION_MINUTES` (default `5`)
  - `POOL_SIZE` (default `5`)

Both recipes emit `junit.xml` under `clusterloader2/`; the density recipe also captures a `SandboxStartupLatency` measurement per the existing agent-sandbox load-test pattern. Sandbox templates for the recipes live under `tests/loadtest/templates/` in this example.

### Skip-stub files (this round)

Bodies written but `@pytest.mark.skip(reason=..., strict=True)` — un-skip when the referenced feature lands. Skip reasons are self-contained:

- `test_cron_gateway.py` — `/v1/cron/next` tests skipped with:
  > `"OpenClaw v2026.3.23 does not expose /v1/cron/next; endpoint is planned per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/openclaw_idle_and_wake.md#2-inner-cron-integration--dynamic-pre-wakeup-jobs. Un-skip once the OpenClaw image with this endpoint ships."`
- `test_cron_gateway.py` — external-DB tests skipped with:
  > `"External Postgres/Spanner backend for cron/memory tables is a future architecture per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/massive_scaling_openclaws.md. Un-skip once OpenClaw supports pointing SQLite-backed stores at an external DB."`

## Assumptions (please flag any that are wrong)

1. **OpenClaw target.** Tests pin to `ghcr.io/openclaw/openclaw:2026.3.23` (upstream, as in [`openclaw-template.yaml`](openclaw-template.yaml)). Source-code references verified at git tag [`v2026.3.23`](https://github.com/openclaw/openclaw/tree/v2026.3.23) on [`github.com/openclaw/openclaw`](https://github.com/openclaw/openclaw).
2. **Plan-doc canonicity.** The design docs at [`tomergee/agent-sandbox` tree `openclaw-integration:plan/`](https://github.com/tomergee/agent-sandbox/tree/openclaw-integration/plan) are the source of truth for endpoints/CRs the eventual Lifecycle Daemon exposes.
3. **Sandbox controller behavior.** Upstream `agent-sandbox` on `main` already honors `spec.operatingMode = Suspended → Running` with PVC preservation. Verified in code:
   - Suspend path at [`controllers/sandbox_controller.go:728-757`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/controllers/sandbox_controller.go#L728-L757) — when `spec.OperatingMode == Suspended`, controller deletes the owned pod and clears the pod-name annotation. No PVC operation in this path.
   - PVC reconciliation at [`controllers/sandbox_controller.go:1093-1166`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/controllers/sandbox_controller.go#L1093-L1166) runs on every reconcile, uses `Get` first and only `Create`s if not-found — so resume finds the existing PVC and the new pod mounts it.
4. **Sessions are on the PVC.** Per OpenClaw source ([`src/config/sessions/`](https://github.com/openclaw/openclaw/tree/v2026.3.23/src/config/sessions), fed into the memory index by [`src/memory/session-files.ts`](https://github.com/openclaw/openclaw/blob/v2026.3.23/src/memory/session-files.ts)), sessions persist under `<stateDir>/sessions/…` — so the MVP PVC path preserves session state. This contradicts the "Active TCP/WebSocket Connections: Disconnected" and "Node.js/Python Timers … Lost" rows in the PVC-only column of the plan-doc comparison table ([`plan/snapshot_techniques_comparison.md`](https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/snapshot_techniques_comparison.md)) — the plan doc is right about *in-memory* state being lost (connections, timers, sleeping shells) but understates that *session records* themselves are on disk. Tests will assert what the code actually does; the plan doc should be reconciled later.
5. **FakeLLM approach is compatible with OpenClaw.** OpenClaw supports `openai-completions`-compatible custom providers via `openclaw.json`. The tests will inject a config block of the form documented at [`openclaw/docs/providers/litellm.md`](https://github.com/openclaw/openclaw/blob/v2026.3.23/docs/providers/litellm.md):
   ```json5
   {
     models: {
       providers: {
         fakellm: {
           baseUrl: "http://localhost:PORT",   // aiohttp fake in-process
           apiKey: "sk-test-nonempty",
           api: "openai-completions",
           models: [{ id: "fake-claude", name: "Fake Claude", input: ["text"],
                      contextWindow: 200000, maxTokens: 8192 }],
         },
       },
     },
     agents: { defaults: { model: { primary: "fakellm/fake-claude" } } },
   }
   ```
   API-key validation is a "non-empty string" check only ([`src/plugins/provider-auth-input.ts:52-53`](https://github.com/openclaw/openclaw/blob/v2026.3.23/src/plugins/provider-auth-input.ts#L52-L53)), so `sk-test-nonempty` passes. A local aiohttp server speaking OpenAI-completions is enough to fake the whole LLM path deterministically and record every prompt.
6. **Automated auth via file seeding.** Since OpenClaw has no `--disable-pairing` flag, tests will pre-seed `/workspace/.openclaw/devices/paired.json` before the pod boots, using the `PairedDevice` schema from [`src/infra/device-pairing.ts`](https://github.com/openclaw/openclaw/blob/v2026.3.23/src/infra/device-pairing.ts#L59-L74). (Template hardened in commit `4872f65` — runs as uid 1000, capabilities dropped, `HOME=/workspace`; PVC mount moved from `/root/.openclaw` to `/workspace/.openclaw`. Any seeding mechanism must write as uid 1000 / fsGroup=1000.):
   ```ts
   export type PairedDevice = {
     deviceId: string;
     publicKey: string;
     displayName?: string;
     platform?: string;
     deviceFamily?: string;
     clientId?: string;
     clientMode?: string;
     role?: string;
     roles?: string[];
     scopes?: string[];
     approvedScopes?: string[];
     remoteIp?: string;
     tokens?: Record<string, DeviceAuthToken>;
     createdAtMs: number;
     approvedAtMs: number;
   };
   ```
   Combine with `--auth none` at the gateway to bypass gateway-level token auth. No changes to OpenClaw needed.
7. **Skip-tag over delete.** Tests for features not yet built (CRIU, `/v1/health/idle`, `/v1/cron/next`, external DB, pre-wakeup controller) are written now, marked `@pytest.mark.skip(reason=...)`, and un-skipped as each plan PR lands. Bias is against deleting the intent because a dependency isn't ready.
8. **Snapshot lifecycle uses the shipped extension.** [`PodSnapshotSandboxClient`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots) is treated as a supported dependency for Group 3 tests. Precondition: **GKE standard cluster ≥ 1.35.2-gke.1842000 with gVisor + Pod Snapshot Controller + GCS bucket**. On kind or GKE without Pod Snapshot, Group 3 tests are correctly skipped by the `@pytest.mark.live` filter, not silently passing.
9. **Wake-on-traffic live test uses the real router.** Deploy [`sandbox-router/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/sandbox-router) (from [PR #838](https://github.com/kubernetes-sigs/agent-sandbox/pull/838)) into the test cluster; test drives WS traffic through it against a suspended OpenClaw sandbox. The wake trigger currently uses a fake stand-in for the Lifecycle Daemon until that daemon is built (plan PR 2) — see Q10 below.
10. **Density & performance ship as ClusterLoader2 recipes**, not pytest tests. Recipes live under this example's own `tests/loadtest/` subfolder (`openclaw-density-test.yaml`, `openclaw-throughput-test.yaml`, matching driver scripts, plus a `templates/` subdir). The shared [`dev/load-test/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test) directory is the *reference* for how ClusterLoader2 is invoked in this repo — we borrow conventions from it but don't add OpenClaw-specific recipes there. Invocation matches the existing flow — `./clusterloader2 --testconfig=... --provider=gke|kind`.
11. **E2E porting is planned as a follow-up stage.** The pytest scenarios in this PR are structured so their invariants can be lifted into a Go-based e2e harness later, following the [`dev/load-test/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test) style. No e2e harness in this PR.
12. **Not in this PR.** Live-harness `scenarios.py` (Markdown report artifact) + full Group 4 coverage (needs `/v1/cron/next`) + live-side idleness tests (needs `/v1/health/idle`) + `SandboxClaim` mirroring (belongs with plan PR 1) + E2E port + external-DB tests. This PR ships: scaffolding + Group 7 (bootstrap, unit-fake + one live variant) + Group 1 (memory, all unit-fake) + Group 2 (PVC, two live tests) + Group 3 (snapshot, all live via `PodSnapshotSandboxClient`) + Group 5 (idleness, unit-fake only) + Group 6 (connections, three unit-fake tests + two live tests, one of which is wake-on-traffic through the real router) + Group 8 (two ClusterLoader2 recipes + shell driver scripts) + skip-stubs for Group 4.

## Open questions for the reviewer

1. **OpenClaw provenance.** The pinned repo [`github.com/openclaw/openclaw`](https://github.com/openclaw/openclaw) reports (from [`GET /repos/openclaw/openclaw`](https://api.github.com/repos/openclaw/openclaw)):
   ```
   created_at:      2025-11-24T10:16:47Z
   stargazers_count: 382,328
   forks_count:      80,229
   license:          NOASSERTION
   size:             1,706,017 KiB (~1.6 GB)
   ```
   That star count for an 8-month-old project would place it in GitHub's top 50 repositories overall — anomalous for a project this new. Two sub-questions:
   - Is [`openclaw/openclaw`](https://github.com/openclaw/openclaw) the correct target, or is there a canonical fork we should pin instead?
   - Is the licensing story (`NOASSERTION`) acceptable for a `kubernetes-sigs/agent-sandbox` example directory, or does it need explicit resolution before we cite the repo in this suite?
2. **Scope confirmation.** Does the first-PR test inventory above match your expectations, or should we trim (memory-only) / expand (add the live harness skeleton)?
3. **Python package name.** `pyproject.toml` needs a distribution name — `openclaw-gvisor-sandbox-tests`? `agent-sandbox-openclaw-tests`? Any repo-wide convention we should match?
4. **Live-test marker convention.** Proposal is `@pytest.mark.live` for the two Group 2 tests that need a real cluster; default `pytest` excludes them, `pytest -m live` runs only those. Is there an existing marker convention in the repo we should mirror?
5. **Skip-stub linkage.** Skip reasons reference [`tomergee/agent-sandbox` `openclaw-integration:plan/*.md`](https://github.com/tomergee/agent-sandbox/tree/openclaw-integration/plan) directly (see the skip-stub reason strings above). Once those docs land upstream, we retarget the URLs. Acceptable, or would you prefer skip reasons reference GitHub issue numbers / a tracking issue in this repo instead?
6. **Divergence handling (Assumption 4).** The plan doc [`snapshot_techniques_comparison.md`](https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/snapshot_techniques_comparison.md) implies more short-term state is lost on PVC-only suspend than the current OpenClaw code actually loses (sessions are persisted). Should the tests just assert current behavior and note the divergence in a comment, or should we also file a docs-fix PR against the plan branch?
7. **Density & performance recipe layout inside `tests/loadtest/`.** Proposal is a flat `tests/loadtest/` with `openclaw-density-test.yaml`, `openclaw-throughput-test.yaml`, matching `run_*.sh` drivers, and a `templates/` subdir — mirroring the layout used by [`dev/load-test/test-recipes/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/dev/load-test). Acceptable, or would you prefer a different internal shape (e.g., recipes and drivers in separate subfolders)?
8. **Density & performance targets.** Sensible default cluster sizes and QPS targets for OpenClaw sandboxes: what N sandbox counts do you want us to sweep (e.g., `10, 50, 100`)? What sustained QPS is realistic for a chat gateway with FakeLLM in the loop (e.g., `10 qps × 5 min`)? Are these numbers you want us to derive experimentally against a reference cluster, or do you have targets from the plan?
9. **Bootstrap responsiveness deadline.** `test_gateway_root_responds_within_deadline` needs a numeric threshold. Proposal: **1500 ms** for the live variant against a warm pod (generous — actual response should be <100 ms), tight enough to catch regressions like a blocking init step. Change?
10. **Wake-on-traffic + missing Lifecycle Daemon.** The live wake-on-traffic test needs *something* to accept the router's resume call. Two options: (a) a minimal test-side HTTP handler that patches `operatingMode=Running` directly (stand-in for the daemon, ~30 lines); (b) wait for plan PR 2 to build the real daemon and gate this test on it. Proposal is (a) so the test can ship now and the handler is replaced with the real daemon once it exists — sound?
11. **ClusterLoader2 execution cadence.** Density/perf recipes are opt-in and manual today. Should we also wire a nightly cron for them (against which cluster?), or leave invocation entirely to whoever's running load tests?
