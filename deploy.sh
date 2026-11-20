#!/usr/bin/env bash
set -euo pipefail

# Deploy an exact, clean commit as a no-traffic Cloud Run staging revision.
# This script never reads .env, creates cloud resources, changes IAM, or switches Meta.

required=(
  RELEASE_SHA GCP_PROJECT_ID GCP_REGION CLOUD_RUN_SERVICE_NAME
  CLOUD_RUN_SERVICE_ACCOUNT CLOUD_SQL_INSTANCE CLOUD_RUN_ORIGIN
  DASHBOARD_DEMO_USERNAME DASHBOARD_DEMO_MERCHANT_ID
  META_PHONE_NUMBER_ID GCS_BUCKET_NAME
  DATABASE_URL_SECRET GEMINI_API_KEY_SECRET DASHBOARD_SESSION_SECRET_NAME
  DASHBOARD_DEMO_PASSWORD_SECRET INTERNAL_SEND_TOKEN_SECRET
  META_VERIFY_TOKEN_SECRET META_WHATSAPP_TOKEN_SECRET META_APP_SECRET_NAME
  WHATSAPP_PHONE_MAP_SECRET
)

for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Required deployment setting is missing: ${name}" >&2
    exit 2
  fi
done

command -v gcloud >/dev/null || {
  echo "Google Cloud CLI is required." >&2
  exit 2
}

actual_sha=$(git rev-parse HEAD)
if [[ "${actual_sha}" != "${RELEASE_SHA}" ]]; then
  echo "HEAD ${actual_sha} does not match RELEASE_SHA ${RELEASE_SHA}." >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing to deploy a dirty worktree." >&2
  exit 2
fi

tag="release-${RELEASE_SHA:0:8}"

# Audit only: all referenced resources must already exist.
gcloud sql instances describe "${CLOUD_SQL_INSTANCE##*:}" \
  --project "${GCP_PROJECT_ID}" --format='value(connectionName)' >/dev/null
gcloud run services describe "${CLOUD_RUN_SERVICE_NAME}" \
  --project "${GCP_PROJECT_ID}" --region "${GCP_REGION}" \
  --format='value(metadata.name)' >/dev/null

for secret in \
  "${DATABASE_URL_SECRET}" "${GEMINI_API_KEY_SECRET}" \
  "${DASHBOARD_SESSION_SECRET_NAME}" "${DASHBOARD_DEMO_PASSWORD_SECRET}" \
  "${INTERNAL_SEND_TOKEN_SECRET}" "${META_VERIFY_TOKEN_SECRET}" \
  "${META_WHATSAPP_TOKEN_SECRET}" "${META_APP_SECRET_NAME}" \
  "${WHATSAPP_PHONE_MAP_SECRET}"; do
  gcloud secrets describe "${secret}" --project "${GCP_PROJECT_ID}" \
    --format='value(name)' >/dev/null
done

gcloud run deploy "${CLOUD_RUN_SERVICE_NAME}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --source . \
  --service-account "${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --add-cloudsql-instances "${CLOUD_SQL_INSTANCE}" \
  --allow-unauthenticated \
  --no-traffic \
  --tag "${tag}" \
  --set-env-vars "DATABASE_BACKEND=postgres,ENVIRONMENT=production,GCP_PROJECT_ID=${GCP_PROJECT_ID},GCP_REGION=${GCP_REGION},TRUSTED_ORIGINS=${CLOUD_RUN_ORIGIN},DASHBOARD_DEMO_USERNAME=${DASHBOARD_DEMO_USERNAME},DASHBOARD_DEMO_MERCHANT_ID=${DASHBOARD_DEMO_MERCHANT_ID},META_PHONE_NUMBER_ID=${META_PHONE_NUMBER_ID},GCS_BUCKET_NAME=${GCS_BUCKET_NAME},CLANDEXIS_RELEASE_SHA=${RELEASE_SHA}" \
  --set-secrets "DATABASE_URL=${DATABASE_URL_SECRET}:latest,GEMINI_API_KEY=${GEMINI_API_KEY_SECRET}:latest,DASHBOARD_SESSION_SECRET=${DASHBOARD_SESSION_SECRET_NAME}:latest,DASHBOARD_DEMO_PASSWORD=${DASHBOARD_DEMO_PASSWORD_SECRET}:latest,INTERNAL_SEND_TOKEN=${INTERNAL_SEND_TOKEN_SECRET}:latest,META_VERIFY_TOKEN=${META_VERIFY_TOKEN_SECRET}:latest,META_WHATSAPP_TOKEN=${META_WHATSAPP_TOKEN_SECRET}:latest,META_APP_SECRET=${META_APP_SECRET_NAME}:latest,WHATSAPP_DEMO_PHONE_MAP=${WHATSAPP_PHONE_MAP_SECRET}:latest"

staging_url=$(gcloud run services describe "${CLOUD_RUN_SERVICE_NAME}" \
  --project "${GCP_PROJECT_ID}" --region "${GCP_REGION}" \
  --format="value(status.traffic[?tag=='${tag}'].url)")

echo "Staging revision deployed with zero production traffic."
echo "Release SHA: ${RELEASE_SHA}"
echo "Staging URL: ${staging_url}"
echo "Meta remains unsubscribed; verify staging before registering its webhook."
