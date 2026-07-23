# Pod Snapshot Python SDK E2E Regression Harness

This directory contains the end-to-end regression test suite for the `k8s-agent-sandbox` Python SDK (`PodSnapshotSandboxClient`) backed by GKE Pod Snapshots.

---

## Overview & Architecture

The test harness validates the full lifecycle of gVisor-protected sandboxes backed by GKE Pod Snapshots against a live Kubernetes cluster and Cloud Storage bucket. It verifies:
- **Sandbox Creation & WarmPool Claims**: Provisioning gVisor sandboxes from `SandboxWarmPool` resources.
- **Sequential Pod Snapshots**: Triggering, listing, and metadata readiness verification for `PodSnapshot` CRs.
- **State Preservation via Suspend & Resume**: Suspending active sandboxes with checkpoint snapshots, restoring container memory/disk state upon resume, and verifying `restored_from_snapshot` flags.
- **Multi-Tenant & Security Isolation**: Multi-tenant concurrency across independent namespaces, verifying snapshot list isolation, default-deny RBAC posture (`kubectl auth can-i`), cross-tenant deletion prohibition, and cross-tenant restore prohibition.
- **Snapshot Teardown**: Specific UID snapshot deletion and bulk `delete_all()` execution.

---

## File Structure

```text
dev/regression-test/podsnapshot-pythonsdk/
├── README.md                   <-- Harness documentation & execution guide (this file)
├── pre-test.sh                 <-- Environment setup: GCS bucket, CRDs, WI & namespace setup
├── run.sh                      <-- Test runner: venv setup, SDK install, pytest execution, trap teardown
├── post-test.sh                <-- Teardown script: force CR cleanup, WI IAM removal, bucket wipe
├── requirements.in             <-- Source requirement bounds
├── requirements.txt            <-- Pinned dependencies generated via pip-compile
├── conftest.py                 <-- Pytest fixtures, fail-fast env validation, client teardowns
├── test_single_user_lifecycle.py <-- Single-user full lifecycle test suite
├── test_multi_user_lifecycle.py  <-- Multi-user concurrent security isolation test suite
└── manifests/                  <-- Declarative K8s manifests applied via envsubst
    ├── sandbox_template.yaml
    ├── sandbox_warmpool.yaml
    ├── podsnapshot_storage_config.yaml
    └── podsnapshot_policy.yaml
```

---

## Harness Lifecycle Breakdown

### 1. Setup Phase (`pre-test.sh`)
- **Precondition Checks**: Verifies GKE Pod Snapshot CRDs, GCP Project ID, Project Number, and Workload Identity configuration on the target cluster.
- **Storage Fabric**: Provisions a Cloud Storage bucket configured for Pod Snapshots (`--enable-hierarchical-namespace`, `--uniform-bucket-level-access`, `--soft-delete-duration=0s`).
- **IAM Bindings**: Grants `roles/storage.objectUser` to the GKE Engine Robot Service Account (`service-${PROJECT_NUMBER}@container-engine-robot.iam.gserviceaccount.com`) and KSA Workload Identity principals.
- **Declarative Manifests**: Applies `sandbox_template.yaml`, `sandbox_warmpool.yaml`, `podsnapshot_storage_config.yaml`, and `podsnapshot_policy.yaml` across tenant namespaces (`tenant-single`, `tenant-alpha`, `tenant-beta`) via `envsubst`.

### 2. Execution Phase (`run.sh` & Pytest)
- **Virtual Environment**: Prepares a Python `.venv`, installs dependencies from `requirements.txt`, and editable-installs the local Python SDK (`clients/python/agentic-sandbox-client/[test]`).
- **Pytest Suite Execution**: Runs `test_single_user_lifecycle.py` and `test_multi_user_lifecycle.py`, generating a JUnit XML report (`results.xml`).
- **Trap Teardown**: Uses a bash EXIT trap (`trap 'rc=$?; "${SCRIPT_DIR}/post-test.sh" || true; exit $rc' EXIT`) to ensure `post-test.sh` executes while preserving pytest exit codes for CI.

### 3. Teardown Phase (`post-test.sh`)
- Force-clears child custom resources (`sandboxes`, `sandboxclaims`, `podsnapshotmanualtriggers`, `podsnapshots`) with `--grace-period=0 --force` to prevent finalizer hangs.
- Removes Workload Identity policy bindings and deletes tenant namespaces.
- Wipes all snapshot storage objects and removes the Cloud Storage bucket (`gcloud storage rm --recursive`).

---

## Prerequisites & Local Execution

### Prerequisites
1. A Kubernetes cluster with gVisor enabled and GKE Pod Snapshot CRDs (`podsnapshot.gke.io/v1`) installed.
2. GCP credentials authenticated (`gcloud auth login` & `gcloud auth application-default login`).
3. Python 3.10+ and `kubectl` CLI tool.

### Running the Harness
To execute the complete regression suite locally:
```bash
./dev/regression-test/podsnapshot-pythonsdk/run.sh
```

### Dependency Management
To update or pin dependencies, edit `requirements.in` and recompile:
```bash
pip-compile --generate-hashes requirements.in
```
