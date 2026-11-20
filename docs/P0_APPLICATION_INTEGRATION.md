# Security and API access

The deployment entry point is `webhook_service.main:app`. It serves the dashboard, extraction API, webhook, and protected outbound endpoint. `app.main:app` also initializes the selected confirmation adapter.

## Configuration

| Variable | Requirement |
| --- | --- |
| `DASHBOARD_SESSION_SECRET` | At least 32 characters; consistent across instances |
| `DASHBOARD_DEMO_USERNAME` | Restricted login name; default `demo` |
| `DASHBOARD_DEMO_PASSWORD` | At least 16 characters |
| `DASHBOARD_DEMO_MERCHANT_ID` | Existing merchant assigned to this login; UUID for PostgreSQL |
| `TRUSTED_ORIGINS` | Exact comma-separated browser origins; include the dashboard origin |
| `ENVIRONMENT` | `production` enables Secure session cookies |
| `INTERNAL_SEND_TOKEN` | Separate service secret of at least 32 characters |
| `META_APP_SECRET` | Required for signed webhook POSTs |
| `META_VERIFY_TOKEN` | Secret matching Meta's GET verification challenge |

Missing/short dashboard credentials disable login. The prepared local launcher creates protected session credentials automatically. Production credentials are supplied through deployment secret management.

## Route access

| Route | Access |
| --- | --- |
| `GET /health`, `/dashboard/` | Public health metadata and static assets |
| `POST /api/v1/auth/login` | Configured restricted credentials |
| `GET /api/v1/auth/session` | Verified signed session |
| `/api/v1/dashboard/*` | Verified session; mutations require CSRF |
| `/api/v1/extract/*`, dashboard speech endpoints | Verified session; mutations require CSRF |
| `GET /webhook` | Matching verification token |
| `POST /webhook` | Valid Meta HMAC, followed by business phone and approved-sender checks |
| `POST /api/v1/send` | `Authorization: Bearer <INTERNAL_SEND_TOKEN>` |

The current application has no `/api/v1/process-inbound` route.

## Session and merchant isolation

Login issues an HttpOnly, SameSite=Strict, one-hour signed session. Production cookies also use Secure. The server assigns the configured merchant to the session. Request `merchant_id` and `confirmed_by` values serve compatibility checks and cannot grant access.

Dashboard/extraction mutations require `X-CSRF-Token` matching the session and reject untrusted browser origins. CORS accepts configured origins. Static frontend assets contain no server credentials.

WhatsApp identity comes from the explicit approved-phone map. PostgreSQL connects under the resolved merchant UUID and applies row-level security. The application's runtime login should have ordinary data permissions, with schema/DDL privileges reserved for administration.

## Session API example

Run against your local or authorized deployment. The password is prompted interactively:

```python
import getpass
import httpx

base = "http://127.0.0.1:8180"
with httpx.Client(base_url=base) as client:
    response = client.post("/api/v1/auth/login", json={
        "username": "demo",
        "password": getpass.getpass("Demo password: "),
    })
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    summary = client.get("/api/v1/dashboard/summary")
    summary.raise_for_status()
    print(summary.json())
```

## Webhook and secret handling

The POST signature is computed over the raw body. Missing, malformed, or incorrect signatures fail closed. The verified payload still passes sender authorization before application processing.

Application logs redact credential-bearing details. Uvicorn query strings are stripped so verification tokens are omitted. Provider failures use safe error messages. Build exclusions cover environment files, private keys, credentials, virtual environments, and generated local artifacts.

Keep actual tokens and passwords in ignored runtime files or secret management. The runtime reads `.env` through Python dotenv loading; files with hyphenated alias names should be loaded by the application.

## Verified coverage

[TESTING.md](TESTING.md) records session tampering, CSRF, CORS, wrong-merchant access, signed webhook verification, outbound protection, and response redaction checks. Production authentication, cloud database access, and signed document delivery must also be exercised on the corrected revision before Meta is reconnected.
