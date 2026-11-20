# 🚀 Clandexis Deployment & Verification Guide

> **Target Platform:** Google Cloud Run (Container Service) + Google Cloud Firestore (Native Mode, `asia-south1`)  
> **GCP Project ID:** `clandexis` | **Region:** `asia-south1`

---

## 1. Architecture & Services

The Clandexis system deploys as a single, high-performance containerized Cloud Run service that serves:
1. **Meta WhatsApp Graph API v21.0 Webhook Receiver** (`POST /webhook` with HMAC-SHA256 signature verification).
2. **Interactive Multimodal Transaction Extractor API** (`/api/v1/extract/text` and `/api/v1/extract/media`).
3. **Claude Canonical Ledger & Codex Confirmation Adapter** (`/api/v1/dashboard/*` & Firestore multi-doc ACID transactions).
4. **Judge & Merchant Live Dashboard** (`GET /dashboard/` - React 18 + Vite + Tailwind CSS static assets).

---

## 2. Environment Configuration & Secret Management

All sensitive secrets are managed via Google Cloud Secret Manager or Cloud Run environment variables.

| Variable Name | Required | Description | Example / Recommended Source |
| :--- | :---: | :--- | :--- |
| `GCP_PROJECT_ID` | **Yes** | Project ID | `clandexis` |
| `GCP_REGION` | **Yes** | Deployment region | `asia-south1` (Mumbai) |
| `PORT` | **Yes** | Container port injected by Cloud Run | `8080` |
| `ENVIRONMENT` | **Yes** | Execution profile | `production` |
| `GEMINI_API_KEY` | **Yes** | API Key for Gemini 3.8/3.5 Flash | Injected from Google Cloud Secret Manager |
| `META_WHATSAPP_TOKEN` | Optional | WhatsApp Cloud API Access Token | Graph API system user token |
| `META_PHONE_NUMBER_ID`| Optional | WABA Phone Number Identifier | Found in Meta App Dashboard |
| `META_APP_SECRET` | Optional | Meta App Secret for HMAC checks | Secret Manager `meta-app-secret` |
| `META_VERIFY_TOKEN` | Optional | Webhook challenge handshake token | String matching Meta Webhook subscription |
| `FIRESTORE_DATABASE` | Optional | Database identifier | `(default)` |
| `HIGH_CREDIT_THRESHOLD` | Optional | Udhaar safety trigger threshold | `5000` (₹5,000 INR) |
| `CONFIDENCE_THRESHOLD` | Optional | Extraction confidence gate | `0.85` |

---

## 3. Container Entry Point & Health Check

- **Entry Point**: `uvicorn webhook_service.main:app --host 0.0.0.0 --port ${PORT}`
- **Health Check Endpoint**: `GET http://localhost:8080/health`
  - Returns `200 OK` with `{"status": "healthy", "service": "webhook-service", "channel": "meta_whatsapp", "api_version": "v21.0"}`

---

## 4. Google Cloud Run Deployment Steps

### Step 1: Authenticate with GCP
```bash
gcloud auth login
gcloud config set project clandexis
```

### Step 2: Enable Required Google Cloud APIs
```bash
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  --project=clandexis
```

### Step 3: Service Account & Minimum IAM Permissions
Ensure the Cloud Run Service Account (`clandexis-runner@clandexis.iam.gserviceaccount.com`) has the minimum required roles:
- `roles/datastore.user` (Read/write access to Cloud Firestore)
- `roles/secretmanager.secretAccessor` (Read API keys from Secret Manager)
- `roles/logging.logWriter` (Write Cloud Logging entries)

### Step 4: Deploy Container to Cloud Run
Run the deployment script from the project root:
```bash
chmod +x deploy.sh
./deploy.sh
```
Or execute directly via `gcloud`:
```bash
gcloud run deploy invisible-erp \
  --source . \
  --region asia-south1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT_ID=clandexis,GCP_REGION=asia-south1,ENVIRONMENT=production \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest
```

---

## 5. Judge Live Dashboard Verification

Once deployed or running locally:
- **Dashboard URL**: `http://<service-url>/dashboard/`
- **Interactive Swagger Docs**: `http://<service-url>/docs`
- **Seeded Merchant Account**: `+919876543210` (Sri Balaji Textiles, Rajesh Kumar)
- **Live Counterparties**: Ramesh Textiles, Suresh Traders
- **Master Inventory**: Cotton Shirts (L) [300 pcs @ ₹200], Linen Kurta (M) [150 pcs @ ₹450]

---

## 6. Exact Remaining Integration Blockers & Mitigations

1. **GCP Application Default Credentials (ADC) on Local Workstations**:
   - *Observation*: Local workstations do not automatically have ADC unless `gcloud auth application-default login` or a service account key is exported in `GOOGLE_APPLICATION_CREDENTIALS`.
   - *Status*: The test suites utilize `test_ledger_emulator.py`, `test_confirmation.py`, and `test_confirmation_ledger_adapter.py` which run in-memory and on mocks without requiring live cloud authentication. Live Cloud Run handles this natively via its attached Compute Engine / IAM service account.
2. **Meta WhatsApp Business Account (WABA) Webhook Validation**:
   - *Observation*: Meta requires a publicly reachable HTTPS callback URL to complete webhook subscription.
   - *Resolution*: Once deployed on Cloud Run, provide `https://<cloud-run-url>/webhook` and the configured `META_VERIFY_TOKEN` in the Meta App Developer Portal.
