# PostgreSQL setup and migration

The application migration is implemented and verified locally. Select it with `DATABASE_BACKEND=postgres` and a configured `DATABASE_URL`. The backend is chosen at process startup, and connection failures remain on that backend. The default setting is `firestore` for existing environments.

The SQL adapter reuses the canonical validator, exact-money calculations, catalog matching, and journal planning. Supported posting intents remain sale, repayment, and stock addition.

## Prepare an empty database

Use PostgreSQL 17+ and [models/postgres_schema.sql](../models/postgres_schema.sql). An administrator should create the database and a dedicated application login, then apply the file once. Configure the administrator's connection through normal protected PostgreSQL connection settings.

```bash
psql -v ON_ERROR_STOP=1 -f models/postgres_schema.sql
```

The script is fresh-install DDL. Existing databases need a separately reviewed migration before this file is applied.

## Add durable WhatsApp onboarding to an existing database

The onboarding service stores unfinished session state and message-id idempotency records in PostgreSQL. Before deploying code that enables this store, apply the additive migration with a migration-owner connection:

```bash
psql -v ON_ERROR_STOP=1 -f models/migrations/20261121_merchant_onboarding.sql
```

Grant the configured runtime role access to the two new tables (replace `clandexis_app` with that role):

```sql
GRANT SELECT, INSERT, UPDATE, DELETE
  ON merchant_onboarding_sessions, merchant_onboarding_events TO clandexis_app;
```

The runtime sets `app.onboarding_phone` from the signed WhatsApp sender identity for each transaction; row-level policies restrict session and event access to that phone. The migration also adds a phone-scoped `SELECT` policy for approved merchant identity resolution, which sets `app.phone_lookup`; it does not widen ordinary merchant data access. Raw inbound text and media are not written to the onboarding tables. Pending business details are treated as expired after 30 days of inactivity and cleared on the next message; activation clears the persisted draft fields. Event records are usable for deduplication for 30 days and expired rows are pruned on subsequent activity from that sender.

Do not run the migration against production or deploy the new service until the target database, runtime grants, and migration result have been reviewed. The migration is not applied automatically at startup.

For an application role named `clandexis_app`, the administrator grants:

```sql
GRANT USAGE ON SCHEMA public TO clandexis_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clandexis_app;
```

Keep schema ownership, DDL, superuser, `BYPASSRLS`, and `TRUNCATE` privileges with administration. Runtime connections set `app.merchant_id` to the merchant UUID resolved from the trusted session or approved phone lookup. Phone resolution sets only `app.phone_lookup` to the signed sender number and can read only the matching approved merchant identity. All other SQL table relationships and operations remain merchant-scoped.

## Configure the application

Add settings to the ignored `.env` or inject them into the process:

| Setting | Purpose |
| --- | --- |
| `DATABASE_BACKEND=postgres` | Activate SQL persistence |
| `DATABASE_URL` | Runtime login and database connection |
| `WHATSAPP_DEMO_PHONE_MAP` | Explicit E.164 phone-to-merchant UUID mapping |
| `DASHBOARD_DEMO_MERCHANT_ID` | Existing UUID assigned to the restricted dashboard login |
| Session, CSRF/origin, Gemini, and invoice settings | See [security](P0_APPLICATION_INTEGRATION.md) and [deployment](DASHBOARD_DEPLOYMENT.md) |

Direct remote TCP connections require `sslmode=verify-full` and an approved CA configuration. The adapter also accepts localhost and Unix-socket connections, including a configured Cloud SQL Auth Proxy connection. It opens tenant-scoped connections without automatic backend failover.

The application installs neither the schema nor cloud resources at startup. The original SQL file shared with the deployment team remains the required schema for this adapter.

## Prepared local environment

| Resource | Location |
| --- | --- |
| PostgreSQL listener | `127.0.0.1:55439` |
| Application database | `clandexis_demo` |
| Isolated test database | `clandexis_app_test` |
| Limited local runtime login | `clandexis_app_test` |
| Durable cluster files | `~/.local/state/clandexis-postgres-demo/pgdata` |
| Generated demo credentials | `~/.local/state/clandexis-postgres-demo/session.json` |
| Local invoice files | `~/.local/state/clandexis-postgres-demo/invoices` |
| Demo backup | `~/.local/state/clandexis-postgres-demo/demo-before-migration-commit.dump` |

This development cluster uses loopback-only authentication. Production requires its own credentials and connection configuration.

```bash
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_demo' \
  PYTHONPATH=. .venv/bin/python scripts/run_postgres_demo.py
```

The launcher can restart the prepared cluster. `PG_CTL` can override the PostgreSQL control executable. It creates a new seed only when its merchant is absent and retains the saved account between restarts. `POSTGRES_DEMO_STATE` selects another explicit local state directory; `POSTGRES_DEMO_PHONE` selects the approved sender when the environment map contains several phones.

Follow [DEMO.md](../DEMO.md) for login and the transaction walkthrough. [TESTING.md](TESTING.md) contains the automated commands and live-model evidence.

## Transfer existing emulator data

`scripts/migrate_local_demo_postgres.py` reads an explicitly selected Firestore emulator merchant and copies it into a separate deterministic PostgreSQL UUID. Set `DATABASE_URL` to the local target, `FIRESTORE_EMULATOR_HOST` to the source emulator, and `GCP_PROJECT_ID` to the source project. Then run:

```bash
PYTHONPATH=. .venv/bin/python scripts/migrate_local_demo_postgres.py \
  --source-merchant SOURCE_MERCHANT_ID \
  --phone APPROVED_E164_PHONE \
  --archive /private/path/source-export.json
```

The importer preserves the source, writes a private archive, retains legacy IDs and source metadata, reconstructs opening balances, and replays committed records through canonical plans. It verifies stock and receivables before reporting `VERIFIED`. Repeating the same export is idempotent. A changed source fingerprint or a conflicting archive stops the import.

Pending candidates and active conversation context are copied. Imported historical messages return a cached historical acknowledgment on replay. Closed historical reviews and original artifact metadata remain in the archive. Unsupported legacy schema versions require an explicit reviewed mapping.

After `VERIFIED`, configure the returned UUID in both `WHATSAPP_DEMO_PHONE_MAP` and `DASHBOARD_DEMO_MERCHANT_ID`, then restart the application. Keep the source and archive available for reconciliation.

The transfer test used a real Firestore emulator source and a real SQL destination. The earlier personal emulator is currently unavailable, and its preserved incident archive remains intact. The running PostgreSQL dashboard is a separately seeded demo; transfer of that personal source remains pending.

## Cloud status

The supplied instance name is `clandexis:asia-south1:clandexis`. Cloud testing awaits the database name, application login, approved test database, and configured connection authentication. The Google-managed Cloud SQL service-agent email identifies infrastructure and does not supply a database login.

The current verification is local. [Deployment requirements](DASHBOARD_DEPLOYMENT.md) cover the remaining Cloud SQL, Cloud Run, GCS, and Meta checks.
