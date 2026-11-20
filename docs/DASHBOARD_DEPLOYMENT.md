# Deployment requirements

The corrected application is verified locally with PostgreSQL. Cloud SQL connectivity, the corrected Cloud Run revision, GCS document delivery, and the real-phone retest remain release checks.

The previous Cloud Run callback served a different application version with an unsafe direct-processing route. Its Meta subscription was disabled. Keep Meta disconnected until that route is contained and the corrected revision is verified. [The incident record](WHATSAPP_P0_INCIDENT.md) preserves the evidence.

## Container and entry point

The existing Dockerfile builds React/Vite assets, installs Python requirements, and serves the frontend/API from one FastAPI process:

```text
uvicorn webhook_service.main:app --host 0.0.0.0 --port ${PORT}
```

Where Docker is available, the build command is:

```bash
docker build -t clandexis:local .
```

Container build and cloud deployment were outside the recorded local verification. The Vite build and Python application tests passed independently. `.dockerignore` excludes runtime environment files, credentials, private keys, virtual environments, and generated artifacts.

The existing `deploy.sh` supplies legacy GCP settings. Prepare explicit PostgreSQL, session, origin, and storage configuration below when deploying the corrected revision.

## Runtime configuration

| Area | Required configuration |
| --- | --- |
| Persistence | `DATABASE_BACKEND=postgres`, `DATABASE_URL` |
| Runtime | `ENVIRONMENT=production`; use the platform's `PORT` |
| Dashboard | `DASHBOARD_SESSION_SECRET`, `DASHBOARD_DEMO_USERNAME`, `DASHBOARD_DEMO_PASSWORD`, existing `DASHBOARD_DEMO_MERCHANT_ID` |
| Browser access | `TRUSTED_ORIGINS` containing the exact public HTTPS origin |
| Extraction | `GEMINI_API_KEY`; optional `GEMINI_MODEL` and `GEMINI_FALLBACK_MODEL` |
| Service outbound | Separate `INTERNAL_SEND_TOKEN` |
| Invoice storage | `GCS_BUCKET_NAME` and storage/signing credentials |
| WhatsApp release | Approved phone map and Meta settings from [integration setup](WHATSAPP_INTEGRATION.md) |

Inject credentials through the team's deployment secret management. Keep environment files out of the image and credential values out of command logs.

## Cloud SQL

The provided instance connection name is `clandexis:asia-south1:clandexis`. It identifies the instance. The application also needs a database name, database login, and configured authentication path. A Google-managed Cloud SQL service-agent email supplies infrastructure identity rather than database login credentials.

1. Install [the schema](../models/postgres_schema.sql) in the selected empty database.
2. Give the application a dedicated runtime role with the privileges described in [PostgreSQL setup](POSTGRES_MIGRATION.md).
3. Configure the approved Cloud SQL Auth Proxy/socket connection or direct TCP connection with `sslmode=verify-full` and its CA.
4. Create/transfer approved demo merchants and configure their UUIDs in the phone map and dashboard account.
5. Exercise the complete flow in the approved isolated database before production cutover.

Every application connection sets the trusted merchant context. Queries from a restricted login without `app.merchant_id` can legitimately return zero rows because of row-level security. Use the authorized merchant UUID when inspecting its data:

```sql
BEGIN;
SELECT set_config('app.merchant_id', 'APPROVED_MERCHANT_UUID', true);
SELECT intent, total_amount, amount_paid, credit_amount
FROM transactions
ORDER BY committed_at DESC;
COMMIT;
```

## Invoice storage

Cloud operation uses the configured GCS bucket when `INVOICE_LOCAL_ROOT` is unset. The runtime identity needs object upload/read access and signing capability for the existing V4 URL resolver. Verify both an authenticated dashboard download and an actual WhatsApp document delivery.

Local filesystem storage is used by the development launcher. Persistent cloud storage must be configured for the deployed service. A signing/upload failure reports `PENDING_RETRY` and leaves the financial commit intact.

## Revision and security verification

Run these checks on the exact deployment that will receive Meta events:

- `GET /health` returns the intended `database_backend`. Confirm actual database reads/writes separately.
- `GET /openapi.json` contains the dashboard/extraction routes. `/api/v1/process-inbound` is absent.
- Anonymous dashboard requests fail, wrong-merchant access fails, and mutations require CSRF.
- The origin allowlist contains the deployed dashboard origin.
- `POST /webhook` rejects invalid signatures; its GET challenge succeeds with the configured token.
- `POST /api/v1/send` requires the internal service token.
- An approved sale commits once, repayment preserves sales/stock, and invoice totals match committed amounts.
- A second approved phone has independent records. Unknown senders receive no merchant access.
- Build metadata and the team's release record identify the exact source commit and deployed revision.

A health response alone establishes process availability. The full checks establish application integration.

## Meta reconnection

After containing the old route and verifying the corrected revision, register that deployment's HTTPS `/webhook`, verify its GET challenge, and subscribe to `messages`. Check the actual business phone resource and the allowed test recipient. Then record evidence for outbound acceptance/delivery, an incoming Meta message ID, clarification, candidate-specific confirmation, voice extraction, and document delivery.

Local mocked transport tests and the previous old-revision conversation provide separate evidence from this corrected live release.
