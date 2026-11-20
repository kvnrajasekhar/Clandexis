#!/bin/bash
set -e

# ==============================================================================
# Clandexis (Invisible ERP) Google Cloud Run Deployment Script
# Automatically loads configuration and secrets from .env if present.
# ==============================================================================

if [ -f .env ]; then
  echo "Found .env file. Loading environment variables..."
  export $(grep -v '^#' .env | xargs)
fi

PROJECT_ID="${GCP_PROJECT_ID:-clandexis}"
REGION="${GCP_REGION:-asia-south1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE_NAME:-invisible-erp-webhook}"

echo "=========================================================="
echo "🚀 Deploying Clandexis Invisible ERP to Google Cloud Run"
echo "Project: $PROJECT_ID | Region: $REGION | Service: $SERVICE_NAME"
echo "=========================================================="

# 1. Set GCP project
gcloud config set project "$PROJECT_ID"

# 2. Build and Deploy container to Cloud Run
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT_ID="$PROJECT_ID",GCP_REGION="$REGION",ENVIRONMENT="${ENVIRONMENT:-production}"

echo "=========================================================="
echo "✅ Deployment completed successfully!"
echo "Service URL:"
gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format 'value(status.url)'
echo "=========================================================="
