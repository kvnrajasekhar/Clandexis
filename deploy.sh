#!/bin/bash
# ==============================================================================
# Clandexis Invisible ERP — Google Cloud Run Deployment Script
# Project: clandexis | Region: asia-south1
# Account: indra.gottipati7@gmail.com
# ==============================================================================
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
PROJECT_ID="clandexis"
PROJECT_NUMBER="972287329524"
REGION="asia-south1"
SERVICE_NAME="invisible-erp-webhook"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
ACCOUNT="indra.gottipati7@gmail.com"

# ── Auth ───────────────────────────────────────────────────────────────────────
echo "🔐 Setting active account: ${ACCOUNT}"
gcloud config set account "${ACCOUNT}"
gcloud config set project "${PROJECT_ID}"

echo ""
echo "=========================================================="
echo "🚀 Clandexis Invisible ERP → Google Cloud Run"
echo "   Project : ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "   Region  : ${REGION}"
echo "   Service : ${SERVICE_NAME}"
echo "   Image   : ${IMAGE_NAME}"
echo "=========================================================="

# ── 1. Enable required APIs ────────────────────────────────────────────────────
echo ""
echo "🔧 [1/5] Enabling required GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  bigquery.googleapis.com \
  cloudkms.googleapis.com \
  --project "${PROJECT_ID}"

# ── 2. Build & push container ──────────────────────────────────────────────────
echo ""
echo "🏗️  [2/5] Building and pushing container via Cloud Build..."
gcloud builds submit . \
  --tag "${IMAGE_NAME}:latest" \
  --project "${PROJECT_ID}" \
  --region "${REGION}"

# ── 3. Provision Secret Manager secrets (idempotent) ──────────────────────────
echo ""
echo "🔑 [3/5] Provisioning Secret Manager secrets..."

create_secret_if_not_exists() {
  local secret_name="$1"
  local description="$2"
  if ! gcloud secrets describe "${secret_name}" --project "${PROJECT_ID}" &>/dev/null; then
    echo "   Creating secret: ${secret_name}"
    gcloud secrets create "${secret_name}" \
      --replication-policy="user-managed" \
      --locations="${REGION}" \
      --project "${PROJECT_ID}"
    echo "   ⚠️  Please add a version for '${secret_name}': gcloud secrets versions add ${secret_name} --data-file=-"
  else
    echo "   ✓ Secret exists: ${secret_name}"
  fi
}

create_secret_if_not_exists "meta-whatsapp-token"       "Meta WhatsApp permanent system user token"
create_secret_if_not_exists "meta-app-secret"           "Meta App secret for HMAC-SHA256 validation"
create_secret_if_not_exists "meta-verify-token"         "Meta webhook verify token"
create_secret_if_not_exists "meta-phone-number-id"      "Meta WhatsApp Phone Number ID"
create_secret_if_not_exists "meta-waba-id"              "Meta WhatsApp Business Account ID"
create_secret_if_not_exists "gemini-api-key"            "Google Gemini API key"
create_secret_if_not_exists "twilio-account-sid"        "Twilio Account SID (backup adapter)"
create_secret_if_not_exists "twilio-auth-token"         "Twilio Auth Token (backup adapter)"

# Grant Cloud Run SA access to secrets
CR_SA="service-${PROJECT_NUMBER}@serverless-robot-prod.iam.gserviceaccount.com"
echo "   Granting Secret Manager accessor to Cloud Run SA: ${CR_SA}"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CR_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None \
  --quiet 2>/dev/null || true

# ── 4. Deploy to Cloud Run ─────────────────────────────────────────────────────
echo ""
echo "☁️  [4/5] Deploying to Cloud Run..."
if [ -f .env ]; then
  echo "   Loading environment variables from .env..."
  export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
fi

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_NAME}:latest" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 10 \
  --concurrency 80 \
  --timeout 60 \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},ENVIRONMENT=production,LOG_LEVEL=INFO,FIRESTORE_DATABASE=(default),BIGQUERY_DATASET=clandexis_analytics,GCS_BUCKET_NAME=clandexis-pdf-artifacts,KMS_KEY_RING=clandexis-attestation-ring,KMS_KEY_NAME=ledger-signing-key,KMS_LOCATION=${REGION},META_API_VERSION=v21.0,META_GRAPH_BASE_URL=https://graph.facebook.com,ROUTER_MODEL=gemini-3.5-flash-lite,EXTRACTOR_MODEL=gemini-3.8-flash,TTS_MODEL=gemini-3.8-flash-tts,ANALYST_MODEL=gemini-4-carbon,CONFIDENCE_THRESHOLD=0.85,HIGH_CREDIT_THRESHOLD=5000.00,META_PHONE_NUMBER_ID=${META_PHONE_NUMBER_ID:-1378627142011047},META_WABA_ID=${META_WABA_ID:-1129045816592822},META_APP_SECRET=${META_APP_SECRET:-3d7dda969f9a175b600f7fd4c56fbc14},META_VERIFY_TOKEN=${META_VERIFY_TOKEN:-clandexis_meta_webhook_verify_token_2026},META_WHATSAPP_TOKEN=${META_WHATSAPP_TOKEN}"

# ── 5. Post-deploy verification ────────────────────────────────────────────────
echo ""
echo "✅ [5/5] Deployment complete!"
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)")

echo ""
echo "=========================================================="
echo "✅ SERVICE DEPLOYED SUCCESSFULLY"
echo "   URL     : ${SERVICE_URL}"
echo "   Health  : ${SERVICE_URL}/health"
echo "   Webhook : ${SERVICE_URL}/webhook"
echo "=========================================================="
echo ""
echo "📋 Meta Developer Portal → Callback URL:"
echo "   ${SERVICE_URL}/webhook"
echo ""
echo "🔍 To view logs:"
echo "   gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE_NAME}' --project ${PROJECT_ID} --limit 50"
