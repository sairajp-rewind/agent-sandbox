#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

ENV_FILE="${SCRIPT_DIR}/.env"

echo "=== Step 1: Reading Environment Configuration ==="
if [ -f "${ENV_FILE}" ]; then
    source "${ENV_FILE}"
else
    echo "INFO: No .env file found; executing graceful fallback cleanup."
fi

echo "=== Step 2: Cleaning Up Kubernetes Test Namespaces & Resources ==="
# Reordered: Kubernetes CRs and Namespaces MUST be deleted BEFORE the GCS bucket is deleted.
# Otherwise, PodSnapshot CR finalizers attempt to reach the missing GCS bucket and freeze namespace deletion in Terminating.
NAMESPACES=("tenant-single" "tenant-alpha" "tenant-beta")

for NS in "${NAMESPACES[@]}"; do
    echo "--- Cleaning Namespace: ${NS} ---"
    
    # 2.1 Force-delete active sandbox claims, sandboxes, manual triggers, and PodSnapshot CRs to clear finalizers
    kubectl -n "${NS}" delete sandboxes,sandboxclaims,podsnapshotmanualtriggers,podsnapshots \
        --all --grace-period=0 --force --ignore-not-found || true
    
    # 2.2 Delete namespace
    kubectl delete namespace "${NS}" --ignore-not-found || true
done

# 2.3 Wait for namespaces to fully terminate (prevents race conditions on subsequent runs)
for NS in "${NAMESPACES[@]}"; do
    echo "Waiting for namespace ${NS} to be fully deleted..."
    kubectl wait --for=delete "namespace/${NS}" --timeout=60s || true
done

echo "=== Step 3: Removing Workload Identity Policy Bindings ==="
if [ -n "${GSA_EMAIL:-}" ] && [ -n "${PROJECT_ID:-}" ]; then
    for NS in "${NAMESPACES[@]}"; do
        echo "Removing Workload Identity binding for ${NS}/sandbox-sa..."
        gcloud iam service-accounts remove-iam-policy-binding "${GSA_EMAIL}" \
            --project="${PROJECT_ID}" \
            --role="roles/iam.workloadIdentityUser" \
            --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NS}/sandbox-sa]" \
            --quiet || true
    done
fi

echo "=== Step 4: Wiping GCS Bucket & Snapshot Artifacts ==="
# Explicitly remove bucket contents first, then delete the bucket container
if [ -n "${BUCKET_NAME:-}" ]; then
    echo "Wiping GCS Bucket artifacts: ${BUCKET_NAME}"
    gcloud storage rm --recursive "${BUCKET_NAME}/**" --quiet || true
    
    echo "Deleting GCS Bucket: ${BUCKET_NAME}"
    if ! gcloud storage buckets delete "${BUCKET_NAME}" --quiet; then
        echo "WARNING: Failed to delete GCS bucket ${BUCKET_NAME}. Bucket may require manual cleanup."
    fi
fi

echo "=== Step 5: Cleaning Up Local State ==="
rm -f "${ENV_FILE}"

echo "Post-test teardown completed successfully."
