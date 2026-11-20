# P0 application integration

## Checkout and integration

Ledger, confirmation bridge, dashboard router, invoice service and webhook application are in the same checkout on `feat/integrate-multimodal-ai`. Integration baseline `368eaaa11fab066babfc801111ae3789e5f4fa32` includes all of these modules. The follow-up P0 commit hardens access, sanitizes failures/logs, and adds application/emulator verification. No second ledger was introduced.

Both `webhook_service.main:app` (deployment entry point) and `app.main:app` import without initializing Gemini or requiring a Gemini key. Both initialize the confirmation adapter during application lifespan. Existing hyphenated service directories retain their tracked Python package symlinks.

The adapter matches Claude's actual `stage_candidate`, `confirm_and_commit`, `cancel_candidate`, `review_candidate`, and `get_transaction` signatures. Snapshot hashes are passed as `expected_snapshot_sha256`. The reviewed snapshot is reread before confirmation. API responses use committed result amounts and persisted inventory movements. Repayments invoke the ledger collection path, do not create a new sale invoice, and do not change stock.

## Authentication configuration

Set secrets through environment/deployment secret management, never frontend source or committed `.env` files:

| Variable | Requirement |
| --- | --- |
| `DASHBOARD_SESSION_SECRET` | Random secret, at least 32 characters, identical on all application instances |
| `DASHBOARD_DEMO_USERNAME` | Restricted demo username; defaults to `demo` |
| `DASHBOARD_DEMO_PASSWORD` | Strong secret, at least 16 characters |
| `DASHBOARD_DEMO_MERCHANT_ID` | The single merchant this demo account may access; must already exist |
| `INTERNAL_SEND_TOKEN` | Separate random service secret, at least 32 characters |
| `TRUSTED_ORIGINS` | Exact comma-separated trusted origins, including dashboard origin; no wildcard |
| `META_APP_SECRET` | Required for all webhook POSTs; unset means reject, including development |
| `META_VERIFY_TOKEN` | Meta verification challenge secret |
| `ENVIRONMENT` | Set `production` in deployed environments to enforce Secure session cookies |

Empty/short session credentials disable demo access. There are no built-in working passwords or public guest sessions. A successful `POST /api/v1/auth/login` issues a signed, HttpOnly, SameSite=Strict, one-hour session cookie. Production cookies are Secure. The signed merchant identity is fixed by server configuration, not login/request merchant fields. Tampered or expired sessions fail verification. Rotate the session secret to invalidate sessions.

All `/api/v1/dashboard/*` routes require the verified session through a router-level dependency. Optional legacy `merchant_id` query/body fields and `confirmed_by` body fields must match the session identity; they never authorize. Every mutation also requires `X-CSRF-Token` matching the signed session and rejects untrusted browser origins. Extraction/speech APIs used by the dashboard likewise require a session; extraction merchant identity must match it. Static dashboard assets remain public and contain no credentials.

Session API example using a password prompt (no password in shell arguments):

```python
import getpass
import requests

base = "https://YOUR-SERVICE"
client = requests.Session()
response = client.post(base + "/api/v1/auth/login", json={
    "username": "demo", "password": getpass.getpass("Demo password: ")
})
response.raise_for_status()
identity = response.json()
client.headers["X-CSRF-Token"] = identity["csrf_token"]
client.get(base + "/api/v1/dashboard/summary").raise_for_status()
response = client.post(base + "/api/v1/dashboard/candidates/confirm", json={
    "candidate_id": "REPLACE_WITH_PERSISTED_CANDIDATE_ID",
    "source_event_id": "unique-dashboard-action-id"
})
print(response.status_code)  # Inspect the authorized transaction result locally.
client.post(base + "/api/v1/auth/logout").raise_for_status()
```

The existing dashboard now presents a sign-in form, reads `/auth/session`, uses its merchant ID and supplies the CSRF header. Its polling starts only after authentication. No server secrets are embedded in the bundle.

`POST /api/v1/send` is service-only: require `Authorization: Bearer <INTERNAL_SEND_TOKEN>`. A dashboard session alone does not permit it. Do not put the send token into browser code.

`POST /webhook` stays publicly reachable and requires the exact Meta HMAC-SHA256 of the raw request body. Missing secret, missing signature, malformed signature and wrong signature all fail closed. GET challenge logs never contain verification tokens. Uvicorn access logs strip query strings, and Meta failure logs omit response bodies, signed media URLs and credential-bearing exception strings. Invalid request/500 responses are generic and never echo passwords or provider exceptions. CORS is an allowlist in both applications. `.dockerignore` excludes environment files, private keys, credentials, virtual environments and local artifacts from the build context.

## Commit -> invoice -> outgoing response

For a signed callback, the existing adapter normalizes the real reply ID. The orchestrator routes it to the confirmation service without extraction. The atomic ledger commit returns before any document/storage/WhatsApp work starts. The worker then:

1. Re-reads the committed transaction and skips invoices for non-sale intents.
2. Generates the basic invoice using committed amounts, stores immutable content, and atomically associates its metadata under the committed transaction.
3. Sends the text response containing the actual transaction ID, paid, credit and inventory balances plus invoice status.
4. Sends the PDF document through the existing Meta dispatcher when its URL is available.

Dashboard confirmation uses the same bridge and post-commit document service. A document failure returns `PENDING_RETRY`; the committed sale remains intact and a repeated callback can regenerate/reuse the artifact. Cancellation and unresolved review never invoke document generation.

Production document dependencies: Firestore ADC credentials, write/read access to `GCS_BUCKET_NAME`, and credentials capable of GCS V4 URL signing. With a runtime identity that cannot sign, invoice delivery reports retry pending instead of inventing a URL or rolling back a sale. Configure signing credentials before deployment. Set `INVOICE_LOCAL_ROOT` only for development or a durable mounted volume; it generates/associates a file but supplies no public Meta URL. No real Meta token changes, messages or production deployments were performed during tests.

## Actual verification

Run from the project root with Java 21+ and firebase-tools:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
tests/run_ledger_tests.sh
```

Or start the real Firestore emulator JAR and run against its explicit address:

```bash
java -jar "$FIRESTORE_EMULATOR_JAR" --host 127.0.0.1 --port 8085 --project_id demo-clandexis
# In another terminal:
PYTHONPATH=. FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 .venv/bin/python -m pytest tests -q
cd dashboard
npm run build
```

The application tests seed independent merchants in the real emulator. Ledger calls, Firestore transactions, confirmation adapter, dashboard HTTP authorization, ReportLab rendering, PDF text validation and artifact metadata association are real. Only external Meta delivery is replaced with a recorder, and test document URLs use `test.invalid`. Tests cover sale confirmation, repayment and outstanding reduction, callback replay, foreign merchant rejection, correction/review followed by confirmation, invoice timing/totals, document failure recovery, CSRF, cookie tampering, production cookie flags, all dashboard route guards, internal send authorization, HMAC enforcement, origin restrictions and secret redaction. The ledger's existing atomicity/concurrency/failure suites also run.

Verified on 2026-11-20 with the real Firestore emulator at `127.0.0.1:8085`: **118 passed, no skips** (5.49 seconds). `npm run build` passed. Both application entrypoints imported without a Gemini API key, and `git diff --check` passed. The pytest-asyncio dependency emits a fixture-scope deprecation warning. The final commit SHA is reported with delivery. Emulator skips are not accepted as a successful integration run. Production connectivity, live Meta delivery and Google service-account signing are deployment checks, not claimed by emulator results.
