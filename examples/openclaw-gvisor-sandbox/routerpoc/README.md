# Router POC: tenant-isolated OpenClaw via sandbox-router

Implements the plan in [`../routerPOC.md`](../routerPOC.md). Two isolated
OpenClaw instances (`tenant-a`, `tenant-b`) sit behind a single
`sandbox-router` reverse proxy; routing is per-request via `X-Sandbox-ID`.

## What this POC proves

Three assertions, in order:

1. **Wire-level routing** — `curl` through the router with
   `X-Sandbox-ID: tenant-a` reaches tenant A's OpenClaw; `tenant-b` reaches
   tenant B's. Automated by [`verify.sh`](verify.sh).
2. **Browser UI loads** — a browser with `X-Sandbox-ID: tenant-a` set via
   any request-header modification extension can open OpenClaw at
   `http://localhost:8080/` and complete pairing. Manual, instructions below.
3. **Tenant isolation** — anything remembered in tenant A's memory stays
   there; tenant B does not see it. Manual, instructions below.

## Layout

| File | Role |
|---|---|
| [`tenant-a.yaml`](tenant-a.yaml) | Tenant A ConfigMap + Template + WarmPool + Claim + Service |
| [`tenant-b.yaml`](tenant-b.yaml) | Tenant B mirror |
| [`router.yaml`](router.yaml) | `sandbox-router` Deployment + Service — the **Python** router at `clients/python/agentic-sandbox-client/sandbox-router/`, built locally as `sandbox-router:poc` (see below), single replica, `ALLOW_UNAUTHENTICATED_ROUTER=true`. **Not the Go router at `/sandbox-router/`** — see "Known non-issues" for why. |
| [`run.sh`](run.sh) | Idempotent end-to-end deploy on kind |
| [`verify.sh`](verify.sh) | Phase 1 wire-level assertion (curl via router) |

## Prerequisite decisions applied

Answering the questions raised in `routerPOC.md`:

- **q1 Namespace model:** single namespace (`default`). All names disambiguated by `-a`/`-b` suffix or `tenant-a`/`tenant-b` naming.
- **q2 Cluster:** kind. See [`../kind-config.yaml`](../kind-config.yaml) and prerequisites in [`../README.md`](../README.md).
- **q3 Service naming:** Services are named `tenant-a` and `tenant-b` explicitly — matching the `X-Sandbox-ID` value the router derives DNS from. Each `SandboxClaim` injects `sandbox.users.io/tenant: tenant-<x>` via `additionalPodMetadata.labels`, and the Service selects on that label. (Only pods adopted by the claim get the label; the warm-pool replenishment pod doesn't, so the Service never routes to a spare.)
- **q4 Router auth:** `ALLOW_UNAUTHENTICATED_ROUTER=true` (Python router env var). POC only — do not port to anything reachable outside the cluster.
- **q5 Router image:** the **Python** router at `clients/python/agentic-sandbox-client/sandbox-router/`, built locally as `sandbox-router:poc` and `kind load`-ed by `run.sh`. Neither router has a published upstream image today — `registry.k8s.io/agent-sandbox/sandbox-router-go` and `.../sandbox-router` both return an empty tag list; the `:latest` string in `sandbox-router/deploy/deployment.yaml` is aspirational (in-tree `sandbox-router/dev/smoke-test/run.sh` sed-swaps it with a locally-built image, and this POC does the same). Originally tried the Go router; had to swap back because it strips `Origin` on WebSocket upgrade and OpenClaw rejects — see "Known non-issues".

## Prerequisites

Same as the parent example — kind cluster with gVisor, `RuntimeClass gvisor`
registered, agent-sandbox core + extensions CRDs installed. See
[`../README.md`](../README.md) for the exact steps if you don't have those yet.

Beyond that: `docker` (for `kind load`-ing OpenClaw + building the router),
`kind`, `kubectl`, `openssl`, `curl`. `run.sh` builds the Python router
locally from `clients/python/agentic-sandbox-client/sandbox-router/`.

## How to run

### One-shot

```bash
cd examples/openclaw-gvisor-sandbox/routerpoc
KEEP_RESOURCES=1 ./run.sh
```

`KEEP_RESOURCES=1` skips the teardown trap so the pods stay up for the
browser-based Phase 2 and Phase 3 tests. Without it, `run.sh` waits for you
to press Enter and then tears everything down.

The script:

1. Verifies `runtimeclass gvisor` exists.
2. Pulls the OpenClaw image, builds `sandbox-router:poc` from
   `clients/python/agentic-sandbox-client/sandbox-router/`, `kind load`s both.
3. Generates one random gateway token per tenant, `sed`-injects into each
   template, applies manifests.
4. Waits for both `SandboxClaim`s to be satisfied and both pods to be ready.
5. Prints the pod names + tokens + step-by-step Phase 1/2/3 instructions.

### Phase 1 (wire-level, automated)

In a second terminal:

```bash
kubectl port-forward svc/sandbox-router-svc 8080:8080
```

Then, from this directory:

```bash
./verify.sh
```

Passes if both `X-Sandbox-ID: tenant-a` and `tenant-b` requests return HTTP
200 with different response bytes. Fresh OpenClaw landing pages sometimes
render byte-identical HTML; when that happens `verify.sh` falls back to
asserting the two Services have distinct ClusterIPs (a weaker check, but
sufficient to prove the router isn't hitting the same pod twice). The
strong end-to-end test is Phase 2/3 anyway.

If you're running the router behind an ingress or IAP tunnel instead of a
plain port-forward, point `verify.sh` at it:

```bash
NO_PORT_FORWARD=1 ROUTER_URL=https://router.example.com ./verify.sh
```

### Phase 2 (browser, manual)

1. Keep `kubectl port-forward svc/sandbox-router-svc 8080:8080` running.
2. In a fresh browser profile, install any request-header modification
   extension (any add-on that can inject custom HTTP request headers per
   URL will do). Configure two headers to send on every request to
   `localhost:8080`:
   ```
   X-Sandbox-ID:  tenant-a
   X-Sandbox-Port: 18789
   ```
3. Browse to `http://localhost:8080/`.
4. When OpenClaw prompts for a gateway token, paste the `tenant-a` token
   `run.sh` printed.
5. Approve the pairing request from the pod (the pod name is also printed
   by `run.sh` — replace `<POD_A>`):
   ```bash
   REQUEST_ID=$(kubectl exec <POD_A> -- node dist/index.js devices list \
     | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
     | head -n 1)
   kubectl exec <POD_A> -- node dist/index.js devices approve "$REQUEST_ID"
   ```
6. Refresh browser. Dashboard loads → Phase 2 passes.

### Phase 3 (isolation, manual)

Continuing from a paired tenant-a session:

1. In OpenClaw chat: *"Please remember that color-A is red."*
2. Verify it landed on tenant-a's PVC:
   ```bash
   kubectl exec <POD_A> -- cat /workspace/.openclaw/workspace/MEMORY.md
   ```
   You should see "color-A".
3. In your header-modification extension, change `X-Sandbox-ID` to
   `tenant-b`. Open a fresh incognito window. Browse `http://localhost:8080/`.
   Pair tenant-b with its own token (same procedure as step 4-5 above,
   but against `<POD_B>`).
4. Ask OpenClaw: *"What do you remember about color-A?"*
   Expected: it doesn't know.
5. Confirm the memory file is clean on tenant-b:
   ```bash
   kubectl exec <POD_B> -- cat /workspace/.openclaw/workspace/MEMORY.md \
     2>/dev/null || echo "(no MEMORY.md — clean)"
   ```

## Cleanup

```bash
cd examples/openclaw-gvisor-sandbox/routerpoc
kubectl delete -f router.yaml

# tenant-a / tenant-b files have a placeholder token in them; sed to any
# value works — kubectl matches resources by metadata, not by the token.
sed 's/dummy-token-tenant-a/x/' tenant-a.yaml | kubectl delete -f -
sed 's/dummy-token-tenant-b/x/' tenant-b.yaml | kubectl delete -f -
```

If `run.sh` was launched without `KEEP_RESOURCES=1`, its trap has already
done this on exit.

## Known non-issues, worth calling out

- **Same pods reachable from `openclaw-gateway` NodePort.** The parent
  example's `kind-service.yaml` selects on `sandbox.users.io/openclaw-claim`
  — a label we do *not* set on these tenants — so the pre-existing
  NodePort Service (if applied) won't accidentally pick up tenant-a/b pods.
  If both the parent example and this POC coexist in the same cluster, they
  stay cleanly separated.
- **`kubectl port-forward` on the tenant Services would fail.** The
  OpenClaw pods run under gVisor, and `port-forward` uses the host kernel's
  view of the pod's netns — which sees nothing listening. That's why the
  POC always accesses OpenClaw *through* the router (whose own pod is not
  gVisor and so is port-forwardable).
- **Why the Python router and not the Go router.** The Go router at
  `/sandbox-router/` hardcodes an `Origin` header strip on WebSocket
  upgrades (`sandbox-router/proxy/proxy.go:207`) as a CSRF defense against
  backends that check `Origin == Host`. OpenClaw is the opposite kind:
  under `--bind=lan`, it *requires* `Origin` be present and match the
  `allowedOrigins` list, and rejects the WS upgrade with
  `code=1008 reason=origin not allowed` (`origin=n/a` in the log line)
  when Origin is stripped. OpenClaw's documented escape hatch
  `dangerouslyAllowHostHeaderOriginFallback: true` did not accept the FQDN
  `Host` the router sends (`tenant-a.default.svc.cluster.local:18789`) —
  empirically still rejected. The Python router at
  `clients/python/agentic-sandbox-client/sandbox-router/` forwards Origin
  unchanged on WS (`WEBSOCKET_HANDSHAKE_HEADERS` in `sandbox_router.py:68`
  excludes only `sec-websocket-*`), which lets the tight allowlist here
  actually match the browser's `http://localhost:8080`. Swap back to Go
  only after either (a) the Go router grows a `--preserve-origin` flag or
  (b) OpenClaw's fallback learns to accept arbitrary Host values.
- **CORS/`allowedOrigins`.** The tenant ConfigMaps list `http://localhost:8080`
  and `http://127.0.0.1:8080` — matching the router's local port-forward.
  If you access via a different origin (a real hostname, a different port),
  update `allowedOrigins` in both ConfigMaps or the UI will fail with an
  origin rejection.
