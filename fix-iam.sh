#!/bin/bash
# ==============================================================================
# Fix Cloud Run public access — must be run as PROJECT OWNER
# Owner: kanagalavnrajasekhar@gmail.com
# ==============================================================================
set -euo pipefail

PROJECT_ID="clandexis"
REGION="asia-south1"
SERVICE_NAME="invisible-erp-webhook"
EDITOR_ACCOUNT="indra.gottipati7@gmail.com"

echo "🔐 Switching to project owner account..."
gcloud config set account kanagalavnrajasekhar@gmail.com
gcloud config set project "$PROJECT_ID"

echo ""
echo "Option A: Make service publicly accessible (unauthenticated)"
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --region="$REGION" \
  --member="allUsers" \
  --role="roles/run.invoker" \
  --project "$PROJECT_ID"

echo ""
echo "Option B: Grant indra.gottipati7 Cloud Run Admin (so they can manage IAM themselves)"
# Uncomment the next 4 lines to run Option B instead of / in addition to Option A:
# gcloud projects add-iam-policy-binding "$PROJECT_ID" \
#   --member="user:${EDITOR_ACCOUNT}" \
#   --role="roles/run.admin" \
#   --condition=None

echo ""
echo "✅ Done! Verifying..."
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" \
  --format "value(status.url)")
echo "Health check: ${SERVICE_URL}/health"
curl -s "${SERVICE_URL}/health"
