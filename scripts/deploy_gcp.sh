#!/usr/bin/env bash
# Deploy the Aegis worker and MCP gateway to Cloud Run in the governed project.
# Run as a project Owner after scripts/gcp_bootstrap.sh. Safe to re-run.
#
#   - Worker (aegis-worker) and gateway (aegis-mcp) run as aegis-reader: no key,
#     no public access (IAM-only invoke, internal ingress for the worker).
#   - Secrets live in Secret Manager; each is readable only by aegis-reader.
#   - aegis-reader gains read-only access to bucket metadata, permission to run
#     BigQuery queries, and permission to write the aegis-audit log.
#   - aegis-pipeline gains a custom role that can only read and update bucket
#     metadata (for merged label fixes); it cannot read or delete objects.
#
# Secret values are read from the repo's gitignored .env (never printed):
#   SUPABASE_SECRET_KEY, SN_OAUTH_CLIENT_SECRET and, if present,
#   GITHUB_APP_PRIVATE_KEY_FILE (path to the GitHub App .pem).
#
# Usage: scripts/deploy_gcp.sh <project-id> [region]

set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project-id> [region]}"
REGION="${2:-australia-southeast1}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
READER="aegis-reader@${PROJECT_ID}.iam.gserviceaccount.com"
PIPELINE="aegis-pipeline@${PROJECT_ID}.iam.gserviceaccount.com"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/aegis"
TAG="$(date -u +%Y%m%d%H%M%S)"

env_value() {  # read KEY from .env without shell expansion
  python3 - "$1" <<'EOF'
import sys
from pathlib import Path
key = sys.argv[1]
for line in Path(".env").read_text().splitlines():
    if line.startswith(key + "="):
        print(line.split("=", 1)[1], end="")
EOF
}

cd "${REPO_DIR}"
gcloud config set project "${PROJECT_ID}" >/dev/null
ME="$(gcloud config get-value account)"

echo "==> APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com logging.googleapis.com storage.googleapis.com

echo "==> Artifact Registry ${REGISTRY}"
gcloud artifacts repositories describe aegis --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create aegis --location="${REGION}" --repository-format=docker \
    --description="Aegis worker and MCP gateway images"

put_secret() {  # name, value
  local name="$1" value="$2"
  if [ -z "${value}" ]; then echo "    skip ${name} (no value)"; return; fi
  gcloud secrets describe "${name}" >/dev/null 2>&1 || gcloud secrets create "${name}" --replication-policy=automatic >/dev/null
  printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=- >/dev/null
  gcloud secrets add-iam-policy-binding "${name}" --member="serviceAccount:${READER}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  echo "    ${name} stored"
}

echo "==> Secrets"
put_secret aegis-supabase-secret-key "$(env_value SUPABASE_SECRET_KEY)"
put_secret aegis-sn-oauth-client-secret "$(env_value SN_OAUTH_CLIENT_SECRET)"
PEM_FILE="$(env_value GITHUB_APP_PRIVATE_KEY_FILE)"
if [ -n "${PEM_FILE}" ] && [ -f "${PEM_FILE}" ]; then
  put_secret aegis-github-app-private-key "$(cat "${PEM_FILE}")"
else
  echo "    skip aegis-github-app-private-key (set GITHUB_APP_PRIVATE_KEY_FILE in .env)"
fi

echo "==> aegis-reader: read bucket metadata, run BigQuery, write the audit log"
for role in roles/storage.bucketViewer roles/bigquery.jobUser roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${READER}" \
    --role="${role}" --condition=None >/dev/null
done

echo "==> aegis-pipeline: custom role to update bucket labels only"
if ! gcloud iam roles describe aegisBucketLabeler --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam roles create aegisBucketLabeler --project="${PROJECT_ID}" \
    --title="Aegis bucket labeler" --description="Read and update bucket metadata for merged Aegis label fixes" \
    --permissions=storage.buckets.get,storage.buckets.update --stage=GA >/dev/null
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${PIPELINE}" \
  --role="projects/${PROJECT_ID}/roles/aegisBucketLabeler" --condition=None >/dev/null

echo "==> Let ${ME} act as aegis-reader for local testing"
gcloud iam service-accounts add-iam-policy-binding "${READER}" --member="user:${ME}" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null

echo "==> Build account aegis-build: read its source upload, push images, write build logs"
BUILDER="aegis-build@${PROJECT_ID}.iam.gserviceaccount.com"
SOURCE_BUCKET="${PROJECT_ID}-aegis-build-source"
gcloud iam service-accounts describe "${BUILDER}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create aegis-build --display-name="Aegis image builds (Cloud Build)"
if ! gcloud storage buckets describe "gs://${SOURCE_BUCKET}" >/dev/null 2>&1; then
  echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > /tmp/aegis-source-lifecycle.json
  gcloud storage buckets create "gs://${SOURCE_BUCKET}" --location="${REGION}" --uniform-bucket-level-access \
    --public-access-prevention --lifecycle-file=/tmp/aegis-source-lifecycle.json
fi
gcloud storage buckets add-iam-policy-binding "gs://${SOURCE_BUCKET}" --member="serviceAccount:${BUILDER}" \
  --role=roles/storage.objectViewer >/dev/null
gcloud artifacts repositories add-iam-policy-binding aegis --location="${REGION}" \
  --member="serviceAccount:${BUILDER}" --role=roles/artifactregistry.writer >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${BUILDER}" \
  --role=roles/logging.logWriter --condition=None >/dev/null

echo "==> Build images in Cloud Build"
cat > /tmp/aegis-cloudbuild.yaml <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "worker/Dockerfile.cloudrun", "-t", "${REGISTRY}/worker:${TAG}", "."]
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "mcp_gateway/Dockerfile", "-t", "${REGISTRY}/mcp:${TAG}", "."]
images: ["${REGISTRY}/worker:${TAG}", "${REGISTRY}/mcp:${TAG}"]
options:
  logging: CLOUD_LOGGING_ONLY
EOF
gcloud builds submit --region="${REGION}" --config=/tmp/aegis-cloudbuild.yaml \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILDER}" \
  --gcs-source-staging-dir="gs://${SOURCE_BUCKET}/source" .

SECRETS="SUPABASE_SECRET_KEY=aegis-supabase-secret-key:latest,SN_OAUTH_CLIENT_SECRET=aegis-sn-oauth-client-secret:latest"
gcloud secrets describe aegis-github-app-private-key >/dev/null 2>&1 && \
  SECRETS="${SECRETS},GITHUB_APP_PRIVATE_KEY=aegis-github-app-private-key:latest"
ENV_VARS="SUPABASE_URL=$(env_value SUPABASE_URL),SN_INSTANCE_URL=$(env_value SN_INSTANCE_URL),SN_OAUTH_CLIENT_ID=$(env_value SN_OAUTH_CLIENT_ID)"
ENV_VARS="${ENV_VARS},SN_APPROVER_GROUP=Aegis Cloud Approvers,GITHUB_REPO=$(env_value GITHUB_REPO),WORKER_ID=aegis-cloudrun"
[ -n "$(env_value GITHUB_APP_ID)" ] && ENV_VARS="${ENV_VARS},GITHUB_APP_ID=$(env_value GITHUB_APP_ID)"
[ -n "$(env_value GITHUB_APP_INSTALLATION_ID)" ] && ENV_VARS="${ENV_VARS},GITHUB_APP_INSTALLATION_ID=$(env_value GITHUB_APP_INSTALLATION_ID)"

echo "==> Deploy aegis-worker (always on, one instance, internal only)"
gcloud run deploy aegis-worker --region="${REGION}" --image="${REGISTRY}/worker:${TAG}" \
  --service-account="${READER}" --no-allow-unauthenticated --ingress=internal \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --cpu=1 --memory=512Mi \
  --set-env-vars="^@^${ENV_VARS//,/@}" --set-secrets="${SECRETS}"

echo "==> Deploy aegis-mcp (IAM-only MCP gateway)"
gcloud run deploy aegis-mcp --region="${REGION}" --image="${REGISTRY}/mcp:${TAG}" \
  --service-account="${READER}" --no-allow-unauthenticated \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID}"

echo
echo "Done. MCP gateway: $(gcloud run services describe aegis-mcp --region="${REGION}" --format='value(status.url)')/mcp"
echo "Grant callers roles/run.invoker on aegis-mcp to let an MCP client connect."
