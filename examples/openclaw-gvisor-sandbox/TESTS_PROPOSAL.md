# OpenClaw Sandbox — Test Suite Proposal (draft)

**Status:** proposal for review — not yet implemented.
**Location:** `examples/openclaw-gvisor-sandbox/tests/`
**Reference:** modeled on [kubernetes-sigs/agent-sandbox#1049](https://github.com/kubernetes-sigs/agent-sandbox/pull/1049) (`agent-sandbox-rl` performance & scale PR, merged 2026-07-07 — 188 mocked pytest tests + a live load-test harness) — same fake-first discipline, leak-check invariants, and separation of unit tests from a live harness.

## Framework

- **Python + pytest**, `pyproject.toml` at the example root (mirrors [`examples/agent-sandbox-rl/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/agent-sandbox-rl) — top-level `pyproject.toml`, `tests/` subdirectory, no source package).
- **Fake-first**: 100% of the first-PR suite runs against hand-written fakes — no cluster required. Runs in seconds on every push.
- **Live harness deferred**: a `scenarios.py` runner (analogous to [`agent-sandbox-rl/tests/loadtest.py`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/examples/agent-sandbox-rl/tests/loadtest.py) — argparse CLI, uncollected by pytest, emits a Markdown report) is a planned follow-up, not this PR. The existing [`run-test-kind.sh`](run-test-kind.sh) continues to serve as the shell smoke path.

## Directory structure

```
examples/openclaw-gvisor-sandbox/
├── README.md                          (existing)
├── openclaw-*.yaml, kind-*.yaml, gke-service.yaml  (existing)
├── run-test-kind.sh                   (existing — shell smoke path)
├── pyproject.toml                     NEW — pytest config, deps
└── tests/                             NEW
    ├── conftest.py                    fakes + autouse leak-check fixtures
    ├── test_memory.py                 Group 1
    ├── test_pvc_preservation.py       Group 2
    ├── test_idleness.py               Group 5
    ├── test_connections.py            Group 6
    ├── test_snapshot_preservation.py  Group 3 — all skip-stubs (CRIU pending)
    └── test_cron_gateway.py           Group 4 — mixed live/skip-stubs
```

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

Unit mirroring test + two live tests (`@pytest.mark.live`, opt-in).

- `test_operating_mode_mirrored_from_sandboxclaim_to_sandbox` (unit)
- `test_pvc_survives_pod_delete_and_respawn` (live, ported from `run-test-kind.sh`)
- `test_pvc_survives_operating_mode_suspend_then_resume` (live)

### `test_idleness.py` — Group 5 (Idleness, unit-fake only)

- `test_idle_endpoint_reports_true_on_fresh_boot`
- `test_idle_endpoint_pendingcount_matches_component_sum`
- `test_lifecycle_daemon_calls_suspend_after_max_idle_time`
- `test_lifecycle_daemon_does_not_suspend_while_pending`

### `test_connections.py` — Group 6 (Connections, unit-fake only)

- `test_concurrent_ws_connections_track_pending_arithmetically`
- `test_ws_disconnect_returns_endpoint_to_idle_after_grace`
- `test_wake_on_traffic_buffers_and_replays_ws_handshake`
- `test_ws_closed_client_side_on_pvc_mode_suspend`

### Skip-stub files

Bodies written but `@pytest.mark.skip(reason=..., strict=True)` — un-skip when the referenced feature lands. Skip reasons are self-contained:

- `test_snapshot_preservation.py` — 5 CRIU tests, each skipped with:
  > `"CRIU / GKE PodSnapshotManualTrigger not available in target cluster. Un-skip when the workflow at https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/checkpoint_restore_workflow.md is deployed and PodRestored=True can be observed on the Sandbox status."`
- `test_cron_gateway.py` — `/v1/cron/next` tests skipped with:
  > `"OpenClaw v2026.3.23 does not expose /v1/cron/next; endpoint is planned per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/openclaw_idle_and_wake.md#2-inner-cron-integration--dynamic-pre-wakeup-jobs. Un-skip once the OpenClaw image with this endpoint ships."`
- `test_cron_gateway.py` — external-DB tests skipped with:
  > `"External Postgres/Spanner backend for cron/memory tables is a future architecture per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/plan/massive_scaling_openclaws.md. Un-skip once OpenClaw supports pointing SQLite-backed stores at an external DB."`

## Assumptions (please flag any that are wrong)

1. **OpenClaw target.** Tests pin to `ghcr.io/openclaw/openclaw:2026.3.23` (upstream, as in [`openclaw-template.yaml`](openclaw-template.yaml)). Source-code references verified at git tag [`v2026.3.23`](https://github.com/openclaw/openclaw/tree/v2026.3.23) on [`github.com/openclaw/openclaw`](https://github.com/openclaw/openclaw).
2. **Plan-doc canonicity.** The design docs at [`tomergee/agent-sandbox` tree `openclaw-integration:plan/`](https://github.com/tomergee/agent-sandbox/tree/openclaw-integration/plan) are the source of truth for endpoints/CRs the eventual Lifecycle Daemon exposes.
3. **Sandbox controller behavior.** Upstream `agent-sandbox` on `main` already honors `spec.operatingMode = Suspended → Running` with PVC preservation (confirmed 2026-07-09).
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
6. **Automated auth via file seeding.** Since OpenClaw has no `--disable-pairing` flag, tests will pre-seed `/root/.openclaw/devices/paired.json` before the pod boots, using the `PairedDevice` schema from [`src/infra/device-pairing.ts`](https://github.com/openclaw/openclaw/blob/v2026.3.23/src/infra/device-pairing.ts#L59-L74):
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
8. **Not in this PR.** Live-harness (`scenarios.py`) + scenario report artifact + full Group 4 coverage + all live-side idleness/connections tests are follow-ups. This PR is scaffolding + the unit-fake half of Groups 1, 2, 5, 6, plus skip-stubs for Groups 3 & 4.

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
