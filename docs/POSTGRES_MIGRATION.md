# PostgreSQL application migration

The application selects exactly one backend through DATABASE_BACKEND. Use postgres with DATABASE_URL to activate the SQL adapter. The default remains firestore for compatibility with existing environments. A failed SQL connection never falls back to Firestore. Durable webhook event records handle deduplication, so a failed provider call can retry the same event without being blocked by the process-local input cache. Restart the process after changing backend settings.

The SQL adapter reuses the existing ledger's validator, decimal calculations, catalog matching and journal planning. The existing supported intents remain sale, repayment and stock addition. Other extracted intents remain held for review. Every SQL connection sets its merchant UUID for the schema's row-level security policies. Remote TCP connections require sslmode=verify-full. Cloud SQL Auth Proxy Unix sockets are also supported.

## Database preparation

Install models/postgres_schema.sql once in an empty PostgreSQL 17+ database. Use a separate runtime login without superuser or BYPASSRLS privileges. Grant that login schema usage and SELECT/INSERT/UPDATE/DELETE on the application tables. Keep schema ownership and DDL privileges with the migration administrator. The application does not provision Cloud SQL instances, change IAM, or install the schema at startup.

The schema shared with the team remains unchanged by the application migration. Candidate hashes use the database's JSONB representation and are returned as opaque revisions to the dashboard and confirmation bridge.

## Local demo on this Mac

An isolated local PostgreSQL cluster listens on 127.0.0.1:55439. Its data directory is ~/.local/state/clandexis-postgres-demo/pgdata and the launcher can restart that existing cluster. A protected demo-before-migration-commit.dump backup is stored alongside it. The application database is clandexis_demo and the limited local test login is clandexis_app_test. The local cluster uses loopback-only development authentication. Production must use its own runtime credentials and verified TLS or Cloud SQL Auth Proxy.

```bash
cd /Users/avinash/Desktop/clandexis/team_clandexis
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_demo' PYTHONPATH=. .venv/bin/python scripts/run_postgres_demo.py
```

Open http://127.0.0.1:8180/dashboard/ and sign in as demo. Copy the password to the clipboard without printing it:

```bash
.venv/bin/python -c 'import json,pathlib,subprocess; p=pathlib.Path.home()/".local/state/clandexis-postgres-demo/session.json"; subprocess.run(["pbcopy"],input=json.loads(p.read_text())["password"].encode(),check=True)'
```

The approved phone comes from POSTGRES_DEMO_PHONE or the sole explicit entry in the ignored WHATSAPP_DEMO_PHONE_MAP. The launcher keeps the same merchant and credentials between restarts, and seeds only a new merchant. It disables Meta transport settings and creates no public tunnel. The initial state is 200 cotton shirts, 150 linen kurtas and zero customer balances. Bootstrap stock is an explicit internal opening entry. Opening entries are omitted from ordinary transaction listings and business transaction counts.

The local smoke test used live Gemini through the HTTP API. Romanized Telugu described selling 50 shirts to Suresh Traders, without price/payment details. The same candidate was clarified first with INR 200 each, then INR 2000 UPI and INR 8000 credit. Sales remained zero before explicit confirmation. After confirmation, sales were INR 10000, UPI INR 2000, outstanding INR 8000 and shirts 150. A duplicate confirmation had no second effect. A live Gemini repayment of INR 3000 left sales and shirts unchanged, raised cumulative UPI to INR 5000 and reduced outstanding to INR 5000. The downloaded PDF retained original paid/due amounts. A PDF ledger request staged nothing. The actual voice-telugu.mp3 upload reached Gemini with staging disabled and did not change the ledger. These checks were performed locally, with no live Meta delivery.

## Verification commands

Use the isolated clandexis_app_test database for repeatable tests. Tests add new isolated merchants and preserve existing records.

```bash
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_app_test' tests/run_postgres_tests.sh
PATH="$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin:$PATH" FIREBASE_BIN="$HOME/.cache/clandexis-tools/fb/node_modules/.bin/firebase" bash tests/run_ledger_tests.sh
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_app_test' PATH="$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin:$PATH" "$HOME/.cache/clandexis-tools/fb/node_modules/.bin/firebase" emulators:exec --only firestore --project demo-clandexis 'PYTHONPATH=. .venv/bin/python -m pytest tests/postgres_migration_checks.py -v'
```

The PostgreSQL application tests use real SQL records, a signed Meta-shaped webhook, authenticated dashboard APIs, real ReportLab rendering and local artifact storage. Gemini and Meta network delivery are stubbed in those repeatable tests. The separate live Gemini smoke test above uses the actual model.

## Existing data transfer

scripts/migrate_local_demo_postgres.py reads an explicitly selected Firestore emulator merchant and copies it into a separate deterministic PostgreSQL merchant UUID. The source is read-only. The importer preserves an on-disk source archive, legacy IDs and original source/candidate metadata, reconstructs opening stock/receivables, and replays committed records through canonical ledger plans. Rerunning the same export is idempotent. A changed source fingerprint stops a resumed import. It verifies stock and receivable totals before reporting VERIFIED.

Pending candidates and active conversation context are copied. Historical conversation events become cached historical acknowledgments so retries cannot restage past transactions. Closed historical review records and source artifacts remain in the source archive. Unsupported legacy schema versions require explicit mapping and stop the import. Never add a partially imported destination UUID to the runtime phone map or dashboard configuration. Use the UUID only after VERIFIED, then update WHATSAPP_DEMO_PHONE_MAP and DASHBOARD_DEMO_MERCHANT_ID together.

The transfer regression test copies an actual canonical Firestore emulator sale and repayment to PostgreSQL, verifies all balances, resumes without duplicates, retains a pending conversation, and checks that source records were not changed. The previously running personal Firestore emulator is currently unavailable. Its incident archive remains preserved. The running SQL dashboard is a separately seeded demo, not an asserted complete transfer of that unavailable emulator.

## Cloud cutover remains pending

The supplied Cloud SQL instance name is clandexis:asia-south1:clandexis. Database login details and an approved isolated database are still required before cloud tests. The Google-managed Cloud SQL service-agent email is not an application database login. Per the latest instruction, this run tests locally first and makes no Cloud SQL or IAM changes.

The old Cloud Run deployment remains untrusted and Meta remains disconnected. Cloud cutover needs the corrected revision, SQL runtime configuration, two-merchant isolation tests and real-phone verification before restoring the subscription.

Final local test results were 8 PostgreSQL application checks, 1 real Firestore-to-PostgreSQL transfer/resume check and 134 existing Firestore checks, all passing. The PostgreSQL schema separately passed its 26 SQL assertions. Cloud testing remains deferred at the user’s request.

A final normal-entry restart exposed an early application import in the demo launcher. The launcher now applies all backend/session settings before importing application modules. A separate-process regression test verifies that initialization order, and the running server was rechecked with HTTP 200 login, PostgreSQL health metadata and the preserved financial totals.
