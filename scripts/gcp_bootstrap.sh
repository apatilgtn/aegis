#!/usr/bin/env bash
# One-off setup of a GCP project for Aegis. Run once, as a project Owner,
# from a machine with the gcloud CLI signed in (`gcloud auth login`).
#
# Creates:
#   - a versioned, private bucket for Terraform state
#   - aegis-pipeline: the only identity that can change IAM, usable ONLY by
#     GitHub Actions on the main branch of this repo (no key is ever created)
#   - aegis-reader: read-only (IAM policy + logs) for the worker; no key created
#   - a Workload Identity pool + GitHub OIDC provider limited to this repo
#
# Safe to re-run: every step skips work that already exists.
#
# Usage: scripts/gcp_bootstrap.sh <project-id> [github-owner/repo]

set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project-id> [github-owner/repo]}"
GITHUB_REPO="${2:-apatilgtn/aegis}"
REGION="us-central1"
STATE_BUCKET="aegis-tfstate-${PROJECT_ID}"
POOL_ID="aegis-github-pool"
PROVIDER_ID="aegis-github-provider"
PIPELINE_SA="aegis-pipeline@${PROJECT_ID}.iam.gserviceaccount.com"
READER_SA="aegis-reader@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
echo "Project ${PROJECT_ID} (number ${PROJECT_NUMBER}), GitHub repo ${GITHUB_REPO}"

echo "==> Enabling APIs"
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  storage.googleapis.com \
  logging.googleapis.com

echo "==> Terraform state bucket gs://${STATE_BUCKET}"
if ! gcloud storage buckets describe "gs://${STATE_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning

create_sa() {
  local name="$1" display="$2"
  if ! gcloud iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" --display-name="${display}"
  fi
}

echo "==> Service accounts"
create_sa aegis-pipeline "Aegis pipeline (GitHub Actions terraform apply only)"
create_sa aegis-reader "Aegis reader (read-only IAM and logs)"

echo "==> Pipeline permissions: project IAM changes + state bucket only"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PIPELINE_SA}" \
  --role="roles/resourcemanager.projectIamAdmin" \
  --condition=None >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
  --member="serviceAccount:${PIPELINE_SA}" \
  --role="roles/storage.objectAdmin" >/dev/null

echo "==> Reader permissions: read IAM policy and logs"
for role in roles/iam.securityReviewer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${READER_SA}" \
    --role="${role}" \
    --condition=None >/dev/null
done

echo "==> Workload Identity Federation for GitHub Actions"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --location=global \
    --display-name="Aegis GitHub Actions"
fi
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="GitHub ${GITHUB_REPO}" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/main'"
fi

gcloud iam service-accounts add-iam-policy-binding "${PIPELINE_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}" >/dev/null

PROVIDER_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

cat <<EOF

Done. Point the GitHub pipeline at this project:

  gh variable set GCP_PROJECT_ID       --repo ${GITHUB_REPO} --body "${PROJECT_ID}"
  gh variable set GCP_WIF_PROVIDER     --repo ${GITHUB_REPO} --body "${PROVIDER_NAME}"
  gh variable set GCP_PIPELINE_SA      --repo ${GITHUB_REPO} --body "${PIPELINE_SA}"

No service account keys were created.
EOF
