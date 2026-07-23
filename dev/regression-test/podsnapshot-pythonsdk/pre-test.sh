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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

ENV_FILE="${SCRIPT_DIR}/.env"

echo "=== Step 1: Precondition Checks ==="

# 1.1 Verify GKE Pod Snapshot CRDs
if ! kubectl get crd podsnapshotstorageconfigs.podsnapshot.gke.io >/dev/null 2>&1; then
    echo "ERROR: GKE Pod Snapshot CRDs (podsnapshotstorageconfigs.podsnapshot.gke.io) are not installed."
    echo "Ensure GKE Pod Snapshots feature is enabled on this cluster."
    exit 1
fi
echo "GKE Pod Snapshot CRDs verified."

# 1.2 Detect GCP Project ID & Project Number
PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo "")"
if [ -z "${PROJECT_ID}" ]; then
    echo "ERROR: Could not detect GCP Project ID. Ensure gcloud is configured."
    exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' 2>/dev/null || echo "")"
if [ -z "${PROJECT_NUMBER}" ]; then
    echo "ERROR: Could not resolve project number for GCP Project '${PROJECT_ID}'."
    exit 1
fi
echo "GCP Project ID: ${PROJECT_ID} (Project Number: ${PROJECT_NUMBER})"

# 1.3 Verify Workload Identity on target cluster
CLUSTER_CONTEXT="$(kubectl config view --minify -o jsonpath='{.contexts[0].context.cluster}' 2>/dev/null || echo "")"
if [[ "${CLUSTER_CONTEXT}" =~ gke_(.+)_([^_]+)_([^_]+) ]]; then
    CLUSTER_PROJECT="${BASH_REMATCH[1]}"
    CLUSTER_LOCATION="${BASH_REMATCH[2]}"
    CLUSTER_NAME="${BASH_REMATCH[3]}"
    echo "Detecting GKE cluster '${CLUSTER_NAME}' in ${CLUSTER_LOCATION}..."
    if [ "${CLUSTER_PROJECT}" != "${PROJECT_ID}" ]; then
        echo "ERROR: gcloud default project (${PROJECT_ID}) differs from cluster project (${CLUSTER_PROJECT})."
        echo "Run: gcloud config set project ${CLUSTER_PROJECT}"
        exit 1
    fi
    WI_POOL="$(gcloud container clusters describe "${CLUSTER_NAME}" --location="${CLUSTER_LOCATION}" --project="${CLUSTER_PROJECT}" --format='value(workloadIdentityConfig.workloadPool)' 2>/dev/null || echo "")"
    if [ -z "${WI_POOL}" ]; then
        echo "ERROR: Target GKE Cluster '${CLUSTER_NAME}' does not have Workload Identity enabled."
        echo "GKE Pod Snapshots require Workload Identity (--workload-pool=${CLUSTER_PROJECT}.svc.id.goog)."
        exit 1
    fi
    echo "Workload Identity verified pool: ${WI_POOL}"
else
    echo "ERROR: kubectl context '${CLUSTER_CONTEXT}' does not match GKE format (gke_PROJECT_LOCATION_NAME)."
    echo "Re-authenticate with 'gcloud container clusters get-credentials'."
    exit 1
fi

echo "=== Step 2: Provisioning GCS Bucket for Pod Snapshots ==="
# GKE Pod Snapshots requires:
# - Hierarchical namespace enabled (--enable-hierarchical-namespace)
# - Uniform bucket-level access (--uniform-bucket-level-access)
# - Soft-delete duration set to 0s (--soft-delete-duration=0s)
BUCKET_HASH="$(openssl rand -hex 4)"
BUCKET_NAME="gs://agent-sandbox-regression-${BUCKET_HASH}"
BUCKET_SHORT="${BUCKET_NAME#gs://}"

echo "Creating GCS Bucket: ${BUCKET_NAME}"
gcloud storage buckets create "${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location=us-central1 \
    --uniform-bucket-level-access \
    --enable-hierarchical-namespace \
    --soft-delete-duration=0s \
    --quiet

echo "=== Step 3: Setting Up GCP Service Account & Storage Bucket IAM ==="
GSA_NAME="podsnapshot-sa"
GSA_EMAIL="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create GCP Service Account if it does not exist
if ! gcloud iam service-accounts describe "${GSA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Creating GCP Service Account: ${GSA_EMAIL}"
    gcloud iam service-accounts create "${GSA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="PodSnapshot Storage Service Account" \
        --quiet
fi

# Grant GCP Service Account objectAdmin access on the GCS bucket
echo "Granting roles/storage.objectAdmin on ${BUCKET_NAME} to ${GSA_EMAIL}"
gcloud storage buckets add-iam-policy-binding "${BUCKET_NAME}" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="roles/storage.objectAdmin" \
    --quiet

# Grant GKE Service Robot Account objectUser access on the GCS bucket (required by GKE PodSnapshot controller)
GKE_ROBOT_SA="service-${PROJECT_NUMBER}@container-engine-robot.iam.gserviceaccount.com"
echo "Granting roles/storage.objectUser on ${BUCKET_NAME} to GKE Robot SA ${GKE_ROBOT_SA}"
gcloud storage buckets add-iam-policy-binding "${BUCKET_NAME}" \
    --member="serviceAccount:${GKE_ROBOT_SA}" \
    --role="roles/storage.objectUser" \
    --quiet

echo "=== Step 4: Deploying agent-sandbox Controller & CRDs ==="
echo "4.1 Deploying agent-sandbox manifests..."
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/latest/download/sandbox-with-extensions.yaml

echo "4.2 Waiting for agent-sandbox CRDs to be established..."
kubectl wait --for=condition=established --timeout=60s \
    crd/sandboxtemplates.extensions.agents.x-k8s.io \
    crd/sandboxwarmpools.extensions.agents.x-k8s.io \
    crd/sandboxclaims.extensions.agents.x-k8s.io \
    crd/sandboxes.agents.x-k8s.io

echo "=== Step 5: Configuring Multi-Tenant Namespaces, Workload Identity & PodSnapshot Policies ==="
NAMESPACES=("tenant-single" "tenant-alpha" "tenant-beta")

for NS in "${NAMESPACES[@]}"; do
    echo "--- Configuring Namespace: ${NS} ---"
    
    # 5.1 Create Namespace and KSA
    kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f -
    kubectl create serviceaccount sandbox-sa -n "${NS}" --dry-run=client -o yaml | kubectl apply -f -

    # 5.2 Workload Identity Binding: Bind KSA to GSA
    gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
        --project="${PROJECT_ID}" \
        --role="roles/iam.workloadIdentityUser" \
        --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NS}/sandbox-sa]" \
        --quiet

    # Annotate KSA with GSA email
    kubectl annotate serviceaccount sandbox-sa -n "${NS}" \
        iam.gke.io/gcp-service-account="${GSA_EMAIL}" \
        --overwrite

    # Grant direct principal binding on GCS bucket for KSA Workload Identity
    KSA_PRINCIPAL="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NS}/sa/sandbox-sa"
    gcloud storage buckets add-iam-policy-binding "${BUCKET_NAME}" \
        --member="${KSA_PRINCIPAL}" \
        --role="roles/storage.objectUser" \
        --quiet

    # 5.3 Apply Manifests via envsubst
    export NS BUCKET_SHORT
    for manifest in \
      "${SCRIPT_DIR}/manifests"/sandbox_template.yaml \
      "${SCRIPT_DIR}/manifests"/sandbox_warmpool.yaml \
      "${SCRIPT_DIR}/manifests"/podsnapshot_storage_config.yaml \
      "${SCRIPT_DIR}/manifests"/podsnapshot_policy.yaml; do
        envsubst '${NS} ${BUCKET_SHORT}' < "${manifest}" | kubectl apply -f -
    done
done

echo "=== Step 6: Exporting Environment Variables to ${ENV_FILE} ==="
cat <<EOF > "${ENV_FILE}"
PROJECT_ID=${PROJECT_ID}
GSA_EMAIL=${GSA_EMAIL}
BUCKET_NAME=${BUCKET_NAME}
TENANT_SINGLE_NS=tenant-single
TENANT_ALPHA_NS=tenant-alpha
TENANT_BETA_NS=tenant-beta
WARM_POOL_NAME=python-counter-pool
EOF

echo "Pre-test setup completed successfully."
