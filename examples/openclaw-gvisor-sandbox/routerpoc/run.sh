#!/usr/bin/env bash
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
#
# End-to-end deploy for the router POC on kind. Idempotent: safe to re-run.
# Cleans up on exit unless KEEP_RESOURCES=1 is set (recommended for
# interactive Phase 2 / Phase 3 browser testing — otherwise the pods will be
# torn down as soon as this script returns).

set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-agent-sandbox}"
ROUTER_IMAGE="${ROUTER_IMAGE:-sandbox-router:poc}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ROUTER_SRC_DIR="${REPO_ROOT}/clients/python/agentic-sandbox-client/sandbox-router"
cd "${SCRIPT_DIR}"

log() { printf '\n=== %s ===\n' "$*"; }

# --- Prechecks --------------------------------------------------------------

for bin in docker kind kubectl openssl awk grep sed; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "ERROR: required binary '${bin}' not found in PATH." >&2
    exit 1
  fi
done

if ! kubectl get runtimeclass gvisor >/dev/null 2>&1; then
  echo "ERROR: RuntimeClass 'gvisor' not found. Register it first — see ../README.md." >&2
  exit 1
fi

if [ ! -f "${ROUTER_SRC_DIR}/Dockerfile" ]; then
  echo "ERROR: expected router Dockerfile at ${ROUTER_SRC_DIR}/Dockerfile — repo layout changed?" >&2
  exit 1
fi

# --- Images: pull OpenClaw + build the Python router locally, load both
# into kind. Python router is used because the Go router at /sandbox-router/
# hardcodes Origin stripping on WebSocket upgrades (proxy/proxy.go:207),
# which OpenClaw rejects. See router.yaml for the full rationale. ---------

OPENCLAW_IMAGE="$(grep -E '^\s+image:' "${SCRIPT_DIR}/tenant-a.yaml" | head -1 | awk '{print $2}' || true)"
if [ -z "${OPENCLAW_IMAGE}" ]; then
  echo "ERROR: could not read OpenClaw image tag from tenant-a.yaml" >&2
  exit 1
fi

log "Pulling ${OPENCLAW_IMAGE}"
docker pull "${OPENCLAW_IMAGE}"

log "Building ${ROUTER_IMAGE} from ${ROUTER_SRC_DIR}"
docker build -t "${ROUTER_IMAGE}" "${ROUTER_SRC_DIR}"

log "Loading images into kind cluster '${KIND_CLUSTER_NAME}'"
kind load docker-image "${OPENCLAW_IMAGE}" --name "${KIND_CLUSTER_NAME}"
kind load docker-image "${ROUTER_IMAGE}" --name "${KIND_CLUSTER_NAME}"

# --- Tokens (one per tenant, injected via sed at apply time) ----------------

TOKEN_A="$(openssl rand -hex 32)"
TOKEN_B="$(openssl rand -hex 32)"

cleanup() {
  if [ "${KEEP_RESOURCES:-0}" = "1" ]; then
    echo
    echo "KEEP_RESOURCES=1 set — leaving all resources in place."
    echo "Tear down manually with:"
    echo "  kubectl delete -f ${SCRIPT_DIR}/router.yaml"
    echo "  sed 's/dummy-token-tenant-a/xxx/' ${SCRIPT_DIR}/tenant-a.yaml | kubectl delete -f -"
    echo "  sed 's/dummy-token-tenant-b/xxx/' ${SCRIPT_DIR}/tenant-b.yaml | kubectl delete -f -"
    return
  fi
  log "Cleaning up (set KEEP_RESOURCES=1 to skip)"
  kubectl delete --ignore-not-found -f "${SCRIPT_DIR}/router.yaml" >/dev/null 2>&1 || true
  sed "s/dummy-token-tenant-a/${TOKEN_A}/g" "${SCRIPT_DIR}/tenant-a.yaml" | kubectl delete --ignore-not-found -f - >/dev/null 2>&1 || true
  sed "s/dummy-token-tenant-b/${TOKEN_B}/g" "${SCRIPT_DIR}/tenant-b.yaml" | kubectl delete --ignore-not-found -f - >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- Apply router + both tenants -------------------------------------------

log "Applying sandbox-router"
kubectl apply -f "${SCRIPT_DIR}/router.yaml"

log "Applying tenant-a (token redacted)"
sed "s/dummy-token-tenant-a/${TOKEN_A}/g" "${SCRIPT_DIR}/tenant-a.yaml" | kubectl apply -f -

log "Applying tenant-b (token redacted)"
sed "s/dummy-token-tenant-b/${TOKEN_B}/g" "${SCRIPT_DIR}/tenant-b.yaml" | kubectl apply -f -

# --- Wait for pods ---------------------------------------------------------

wait_for_claim() {
  local claim="$1"
  local sandbox_name=""
  for _ in $(seq 1 90); do
    sandbox_name="$(kubectl get sandboxclaim "${claim}" -o jsonpath='{.status.sandbox.name}' 2>/dev/null || true)"
    [ -n "${sandbox_name}" ] && break
    sleep 1
  done
  if [ -z "${sandbox_name}" ]; then
    echo "ERROR: SandboxClaim '${claim}' was never satisfied." >&2
    kubectl describe sandboxclaim "${claim}" >&2 || true
    exit 1
  fi

  local pod=""
  for _ in $(seq 1 30); do
    pod="$(kubectl get sandbox "${sandbox_name}" -o jsonpath='{.metadata.annotations.agents\.x-k8s\.io/pod-name}' 2>/dev/null || true)"
    [ -n "${pod}" ] && break
    sleep 1
  done
  if [ -z "${pod}" ]; then
    pod="${sandbox_name}"
  fi
  echo "${pod}"
}

log "Waiting for tenant-a claim"
POD_A="$(wait_for_claim openclaw-claim-a)"
echo "tenant-a pod: ${POD_A}"
kubectl wait --for=condition=ready "pod/${POD_A}" --timeout=180s

log "Waiting for tenant-b claim"
POD_B="$(wait_for_claim openclaw-claim-b)"
echo "tenant-b pod: ${POD_B}"
kubectl wait --for=condition=ready "pod/${POD_B}" --timeout=180s

log "Waiting for sandbox-router deployment"
kubectl rollout status deployment/sandbox-router --timeout=180s

# --- Print how to test -----------------------------------------------------

cat <<EOF

======================================================================
POC is up. Set KEEP_RESOURCES=1 next time to keep pods across script
runs — otherwise the trap above tears everything down when this script
returns.

Pods:
  tenant-a : ${POD_A}
  tenant-b : ${POD_B}

Tokens (needed for the browser pairing step in Phase 2/3):
  tenant-a : ${TOKEN_A}
  tenant-b : ${TOKEN_B}

Phase 1 — wire-level test (curl through router):

  # In another terminal, keep this running:
  kubectl port-forward svc/sandbox-router-svc 8080:8080

  # Then in this terminal:
  ./verify.sh

Phase 2 — browser UI (see README.md for full instructions):

  1. kubectl port-forward svc/sandbox-router-svc 8080:8080
  2. In a fresh browser profile, install any request-header modification
     extension and configure it to send these on every request to
     localhost:8080:
       X-Sandbox-ID:  tenant-a
       X-Sandbox-Port: 18789
  3. Browse http://localhost:8080/
  4. When prompted for a token, paste the tenant-a token printed above.
  5. Approve the pairing request:
       kubectl exec ${POD_A} -- node dist/index.js devices list
       kubectl exec ${POD_A} -- node dist/index.js devices approve <ID>
  6. Refresh browser. Dashboard should load.

Phase 3 — isolation:

  1. In the tenant-a paired session, send:
       "Please remember that color-A is red."
  2. Verify it landed:
       kubectl exec ${POD_A} -- cat /workspace/.openclaw/workspace/MEMORY.md
  3. Switch the header-modification extension to X-Sandbox-ID: tenant-b,
     open an incognito window, browse http://localhost:8080/, pair
     tenant-b with its token.
  4. Ask OpenClaw: "What do you remember about color-A?"
     Expected: it doesn't know.
  5. Verify tenant-b's memory is clean:
       kubectl exec ${POD_B} -- cat /workspace/.openclaw/workspace/MEMORY.md 2>/dev/null || echo "(no MEMORY.md — clean)"
======================================================================
EOF

if [ "${KEEP_RESOURCES:-0}" != "1" ]; then
  echo
  echo "Sleeping until you Ctrl-C (so pods stay up for testing)."
  echo "Re-run with KEEP_RESOURCES=1 to skip this and detach cleanly."
  # shellcheck disable=SC2034
  read -r -p "Press Enter to tear down and exit... " _
fi
