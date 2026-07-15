# OpenClaw Sandbox Integration Tests

This directory contains the test suite for the OpenClaw + Agent Sandbox integration.

## Setup

Requires Python >= 3.10.

Create a virtual environment and install test dependencies:

```bash
cd examples/openclaw-gvisor-sandbox
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
```

## Running Tests

### Unit / Contract Tests (Default)

These tests run entirely in-process using mock fakes (no Kubernetes cluster required):

```bash
pytest
```

### Live Tests (Require a Cluster)

To run the live tests, ensure your current context is pointed at a suitable Kubernetes cluster (e.g. kind or GKE):

```bash
export KUBECONFIG=~/.kube/config              # or your cluster's config path
pytest -m live                                 # runs only live tests
pytest -m "live or not live"                   # runs all tests (live + unit)
```

### Live Test Cluster Requirements

- **PVC (`Group 2`) + Connections (`Group 6`) Live Tests**: Any kind or GKE cluster with gVisor enabled and the example manifests applied.
- **Snapshot (`Group 3`) Live Tests**: A GKE standard cluster (version >= 1.35.2-gke.1842000) with gVisor, Pod Snapshot Controller, GCS bucket, `PodSnapshotStorageConfig`, and `PodSnapshotPolicy` (with the `agents.x-k8s.io/sandbox-name-hash` grouping label) configured. See the [snapshots client documentation](../../../clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots/README.md).
- **Bootstrap (`Group 7`) Live Bootstrap + Memory (`Group 1`) Live Tests**: Additionally require the three items in `plan.md` § 1a (`paired.json` seeded, `openclaw.json` FakeLLM override applied, and FakeLLM reachable from pod). Until those are resolved, these tests auto-skip.

### Gateway Ingress Convention

When running live tests, they require network ingress to hit the OpenClaw gateway:
- **On kind**: The suite uses the NodePort `30789` mapped by `kind-service.yaml` (routing to `http://127.0.0.1:30789`).
- **On GKE**: Use the IAP tunnel or set `OPENCLAW_TEST_GATEWAY_URL=http://localhost:18789` (or the LoadBalancer's URL) to override the target host.
- **CRITICAL WARNING**: Do not use `kubectl port-forward` to access the gateway. Port forwarding is broken under gVisor because the application binds inside gVisor's user-space netstack, meaning `kubectl port-forward` enters the host kernel's view of the pod's network namespace and finds nothing listening.

## Running Specific Tests

To run a single test group or file:

```bash
pytest tests/test_memory.py                      # just memory tests
pytest tests/test_pvc_preservation.py -m live     # just live PVC tests
```