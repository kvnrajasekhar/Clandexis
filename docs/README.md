# Documentation

The current application uses the PostgreSQL adapter when `DATABASE_BACKEND=postgres`. Deployment and live WhatsApp verification remain pending. The guides below describe the verified local application and the requirements for the next release.

| Guide | Use it for |
| --- | --- |
| [Local demo](../DEMO.md) | Open the app, log in, extract, clarify, confirm, repay, download an invoice |
| [PostgreSQL setup and migration](POSTGRES_MIGRATION.md) | Prepare a database, configure the adapter, preserve and transfer existing data |
| [Tests and verified results](TESTING.md) | Reproduce automated checks and understand verification limits |
| [Security and API access](P0_APPLICATION_INTEGRATION.md) | Sessions, CSRF, merchant isolation, webhook HMAC, outbound authorization |
| [WhatsApp integration](WHATSAPP_INTEGRATION.md) | Phone allowlist, credentials, media handling, conversation storage |
| [WhatsApp conversation flow](WHATSAPP_WORKFLOW.md) | Supported messages, clarification, confirmation, and failure behavior |
| [Confirmation and invoice contracts](CONFIRMATION_PDF_INTERFACE.md) | Service interfaces, API endpoints, committed invoice data, artifact retries |
| [Deployment requirements](DASHBOARD_DEPLOYMENT.md) | Correct revision, runtime configuration, Cloud SQL, GCS, and release checks |
| [PostgreSQL schema](../models/postgres_schema.sql) | Fresh-install DDL shared with the deployment team |
| [Firestore ledger reference](LEDGER_INTERFACE.md) | Detailed legacy-backend contract and accounting implementation |
| [WhatsApp incident record](WHATSAPP_P0_INCIDENT.md) | Evidence, containment, and unresolved old-deployment investigation |

Operational commands assume the `team_clandexis` repository root. Examples use synthetic phone numbers and placeholder merchant UUIDs. Actual credentials belong in ignored environment files or deployment secret management.
