#!/usr/bin/env bash
# ==============================================================================
# Clandexis (Invisible ERP) - Google Cloud Secret Manager Sync Script
#
# Reads ALL configuration (secrets and non-secrets, NO demo mode) from .env
# and populates Google Cloud Secret Manager.
#
# Usage:
#   chmod +x scripts/sync_secrets_to_gcp.sh
#   ./scripts/sync_secrets_to_gcp.sh [path-to-.env] [project-id]
# ==============================================================================

set -euo pipefail

ENV_FILE="${1:-.env}"
PROJECT_ID="${2:-clandexis}"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ Error: Environment file '$ENV_FILE' not found."
  exit 1
fi

echo "=========================================================="
echo "🔐 Clandexis Secret Manager Provisioning (Full Config - No Demo)"
echo "Project: $PROJECT_ID | Source: $ENV_FILE"
echo "=========================================================="

# Helper function to get variable from env file without evaluating
get_env_val() {
  local key="$1"
  local val
  val=$(grep -E "^${key}=" "$ENV_FILE" | head -n 1 | cut -d'=' -f2-)
  # Strip surrounding quotes if present
  val="${val%\"}"
  val="${val#\"}"
  val="${val%\'}"
  val="${val#\'}"
  echo "$val"
}

# Helper to sync a single secret/setting to Secret Manager
sync_secret() {
  local secret_name="$1"
  local env_key="$2"
  local default_val="${3:-}"
  local val
  val=$(get_env_val "$env_key")

  if [ -z "$val" ]; then
    val="$default_val"
  fi

  if [ -z "$val" ]; then
    echo "⚠️  Skipping '$secret_name' ($env_key is empty or unset in $ENV_FILE)"
    return 0
  fi

  echo "➡️  Syncing '$secret_name' from $env_key..."

  # Create secret if it does not already exist
  if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "   Creating secret '$secret_name'..."
    gcloud secrets create "$secret_name" \
      --replication-policy="automatic" \
      --project="$PROJECT_ID" >/dev/null
  fi

  # Add new version with the value via stdin
  printf "%s" "$val" | gcloud secrets versions add "$secret_name" \
    --project="$PROJECT_ID" \
    --data-file=- >/dev/null

  echo "   ✅ '$secret_name' updated successfully."
}

echo "--- 1. Infrastructure & Platform Configuration ---"
sync_secret "database-backend" "DATABASE_BACKEND" "postgres"
sync_secret "gcp-project-id" "GCP_PROJECT_ID" "$PROJECT_ID"
sync_secret "gcp-region" "GCP_REGION" "asia-south1"
sync_secret "environment" "ENVIRONMENT" "production"
sync_secret "trusted-origins" "TRUSTED_ORIGINS" "https://invisible-erp-webhook-972287329524.asia-south1.run.app"
sync_secret "gcs-bucket-name" "GCS_BUCKET_NAME" "clandexis-pdf-artifacts"
sync_secret "kms-key-ring" "KMS_KEY_RING" "clandexis-attestation-ring"
sync_secret "kms-key-name" "KMS_KEY_NAME" "ledger-signing-key"
sync_secret "kms-location" "KMS_LOCATION" "asia-south1"
sync_secret "meta-api-version" "META_API_VERSION" "v21.0"
sync_secret "meta-graph-base-url" "META_GRAPH_BASE_URL" "https://graph.facebook.com"
sync_secret "google-client-id" "GOOGLE_CLIENT_ID" ""

echo "--- 2. Production Credentials & Tokens ---"
sync_secret "database-url" "DATABASE_URL"
sync_secret "gemini-api-key" "GEMINI_API_KEY"
sync_secret "meta-whatsapp-token" "META_WHATSAPP_TOKEN"
sync_secret "meta-app-secret" "META_APP_SECRET"
sync_secret "meta-phone-number-id" "META_PHONE_NUMBER_ID"
sync_secret "meta-verify-token" "META_VERIFY_TOKEN"
sync_secret "meta-waba-id" "META_WABA_ID"
sync_secret "dashboard-session-secret" "DASHBOARD_SESSION_SECRET"
sync_secret "internal-send-token" "INTERNAL_SEND_TOKEN"

echo "----------------------------------------------------------"
echo "🔑 Applying Secret Manager Accessor role for Cloud Run..."

PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' 2>/dev/null || true)

if [ -n "$PROJECT_NUM" ]; then
  RUN_SA="${PROJECT_NUM}-compute@developer.gserviceaccount.com"
  echo "Attempting to grant 'roles/secretmanager.secretAccessor' to $RUN_SA..."

  # Project-level IAM if permissions allow
  if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$RUN_SA" \
      --role="roles/secretmanager.secretAccessor" \
      --condition=None >/dev/null 2>&1; then
    echo "✅ Project-level IAM permission granted."
  else
    echo "ℹ️  Attempting secret-level IAM bindings..."
    ALL_SECRETS=(
      "database-backend"
      "gcp-project-id"
      "gcp-region"
      "environment"
      "trusted-origins"
      "gcs-bucket-name"
      "kms-key-ring"
      "kms-key-name"
      "kms-location"
      "meta-api-version"
      "meta-graph-base-url"
      "google-client-id"
      "database-url"
      "gemini-api-key"
      "meta-whatsapp-token"
      "meta-app-secret"
      "meta-phone-number-id"
      "meta-verify-token"
      "meta-waba-id"
      "dashboard-session-secret"
      "internal-send-token"
    )

    for s in "${ALL_SECRETS[@]}"; do
      gcloud secrets add-iam-policy-binding "$s" \
        --member="serviceAccount:$RUN_SA" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT_ID" >/dev/null 2>&1 || true
    done
    echo "✅ Secret-level bindings updated."
  fi
fi

echo "=========================================================="
echo "🎉 ALL configurations & secrets stored in Google Cloud Secret Manager!"
echo "No demo mode credentials included. Ready for Cloud Run branch deployment."
echo "=========================================================="
