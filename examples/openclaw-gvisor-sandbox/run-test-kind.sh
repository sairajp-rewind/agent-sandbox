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

set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-agent-sandbox}"
IMAGE="ghcr.io/openclaw/openclaw:2026.3.23"
NODE_PORT="30789"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# --- Prechecks --------------------------------------------------------------

if ! kubectl get runtimeclass gvisor >/dev/null 2>&1; then
  cat <<EOF >&2
ERROR: RuntimeClass 'gvisor' not found in the cluster.

This example requires gVisor. Install runsc on the kind node image and create
the RuntimeClass before running this script. See:
  https://gvisor.dev/docs/user_guide/quick_start/kubernetes/
EOF
  exit 1
fi

# --- Image load -------------------------------------------------------------

echo "Pulling ${IMAGE}..."
docker pull "${IMAGE}"

echo "Loading ${IMAGE} into kind cluster '${KIND_CLUSTER_NAME}'..."
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER_NAME}"

# --- Apply ------------------------------------------------------------------

echo "Generating gateway token..."
TOKEN="$(openssl rand -hex 32)"

echo "Applying manifests..."
kubectl apply -f openclaw-config.yaml
sed "s/dummy-token-for-sandbox/${TOKEN}/g" sandboxtemplate.yaml | kubectl apply -f -
kubectl apply -f sandboxwarmpool.yaml
kubectl apply -f sandboxclaim.yaml
kubectl apply -f service.yaml

cleanup() {
  echo "Cleaning up..."
  kubectl delete --ignore-not-found -f service.yaml
  kubectl delete --ignore-not-found -f sandboxclaim.yaml
  kubectl delete --ignore-not-found -f sandboxwarmpool.yaml
  kubectl delete --ignore-not-found -f sandboxtemplate.yaml
  kubectl delete --ignore-not-found -f openclaw-config.yaml
}
trap cleanup EXIT

# --- Wait for the claim's pod ----------------------------------------------

echo "Waiting for SandboxClaim to be satisfied..."
for i in $(seq 1 60); do
  CLAIM_UID="$(kubectl get sandboxclaim openclaw-sandbox-claim -o jsonpath='{.metadata.uid}' 2>/dev/null || true)"
  [ -n "${CLAIM_UID}" ] && break
  sleep 1
done
if [ -z "${CLAIM_UID:-}" ]; then
  echo "ERROR: SandboxClaim 'openclaw-sandbox-claim' never appeared." >&2
  exit 1
fi

CLAIM_SELECTOR="agents.x-k8s.io/claim-uid=${CLAIM_UID}"
echo "Waiting for claimed pod to be ready (selector: ${CLAIM_SELECTOR})..."
kubectl wait --for=condition=ready pod -l "${CLAIM_SELECTOR}" --timeout=180s

POD="$(kubectl get pods -l "${CLAIM_SELECTOR}" -o jsonpath='{.items[0].metadata.name}')"
echo "Claimed pod: ${POD}"

# --- Gateway reachability via NodePort -------------------------------------

echo "Checking gateway via NodePort localhost:${NODE_PORT}..."
if ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:${NODE_PORT}/"; then
  cat <<EOF >&2
ERROR: NodePort ${NODE_PORT} is not reachable on localhost.

The kind cluster must be created with an extraPortMappings entry for
${NODE_PORT}. Recreate the cluster with the snippet from README.md.
EOF
  exit 1
fi
echo "Gateway responded on NodePort ${NODE_PORT}."

# --- PVC persistence test ---------------------------------------------------

CANARY="persistence-canary-$(openssl rand -hex 4)"
echo "Writing canary to /root/.openclaw/workspace/canary.txt in ${POD}..."
kubectl exec "${POD}" -- sh -c "echo '${CANARY}' > /root/.openclaw/workspace/canary.txt"

EXPECTED="$(kubectl exec "${POD}" -- cat /root/.openclaw/workspace/canary.txt)"
if [ "${EXPECTED}" != "${CANARY}" ]; then
  echo "ERROR: canary write/read mismatch in original pod." >&2
  exit 1
fi

echo "Deleting pod ${POD} to force a respawn..."
kubectl delete pod "${POD}" --wait=true

echo "Waiting for the Sandbox controller to respawn the pod..."
for i in $(seq 1 60); do
  NEW_POD="$(kubectl get pods -l "${CLAIM_SELECTOR}" -o jsonpath='{.items[?(@.metadata.name!="'"${POD}"'")].metadata.name}' | awk '{print $1}')"
  [ -n "${NEW_POD}" ] && break
  sleep 2
done
if [ -z "${NEW_POD:-}" ]; then
  echo "ERROR: replacement pod never appeared under selector ${CLAIM_SELECTOR}." >&2
  exit 1
fi
echo "Replacement pod: ${NEW_POD}"
kubectl wait --for=condition=ready pod "${NEW_POD}" --timeout=180s

ACTUAL="$(kubectl exec "${NEW_POD}" -- cat /root/.openclaw/workspace/canary.txt)"
if [ "${ACTUAL}" != "${CANARY}" ]; then
  echo "FAIL: PVC did not persist across pod respawn." >&2
  echo "  expected: ${CANARY}" >&2
  echo "  actual:   ${ACTUAL}" >&2
  exit 1
fi

echo "PASS: PVC persisted across pod respawn (${CANARY})."
echo "Test finished."
