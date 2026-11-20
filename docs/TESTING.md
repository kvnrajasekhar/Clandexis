# Tests and verification

Run commands from the repository root. Use isolated test accounts and databases. PostgreSQL application tests create new synthetic merchants and financial records; schema checks require a fresh disposable database.

## Recorded results

Verified on 2026-11-20 against application baseline `e5be7bc`:

| Check | Result | Real components | Test doubles |
| --- | --- | --- | --- |
| PostgreSQL application suite | 8 passed | PostgreSQL, row-level security, HTTP auth/CSRF, confirmations, invoices, conversations | Gemini and Meta network calls |
| Firestore-to-PostgreSQL transfer | 1 passed | Firestore emulator source, SQL target, replay and balance reconciliation | External networks |
| Existing Firestore suite | 134 passed, zero skips | Firestore emulator, atomic ledger operations, HTTP integration, PDF rendering | Provider and Meta delivery where isolated |
| PostgreSQL schema checks | 26 assertions passed | Local PostgreSQL 17 and database triggers | None |
| Dashboard production build | Passed | Vite production assets | None |
| Python dependency check | Passed | Installed development environment | None |

These results establish local behavior. Cloud SQL connectivity, corrected Cloud Run deployment, real-phone retest, and GCS document delivery remain pending.

## PostgreSQL application tests

Prepare the isolated database using the current schema and a limited runtime login. On the prepared Mac:

```bash
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_app_test' \
  tests/run_postgres_tests.sh
```

Coverage includes sale, repayment, stock addition, duplicate/concurrent confirmation, two merchants, unknown senders, Hindi/Romanized Telugu clarification, the same candidate across replies, dashboard extraction, CSRF, invoice download/failure, provider retry, and launcher initialization order.

## SQL constraints

Apply the schema to a new disposable database first, then run as its test administrator:

```bash
psql -v ON_ERROR_STOP=1 -f models/postgres_schema.sql
psql -v ON_ERROR_STOP=1 -f tests/sql/postgres_schema_checks.sql
```

Connection settings must point to that disposable database. The script creates synthetic records and a restricted test role. It checks confirmation/review gates, journals, immutability, cross-merchant access, sale/repayment totals, and invoices.

## Firestore regression tests

Java 21+ and `firebase-tools` must be available. The script starts and stops the real emulator:

```bash
DATABASE_BACKEND=firestore tests/run_ledger_tests.sh
```

On the prepared Mac, cached tools can be selected explicitly:

```bash
DATABASE_BACKEND=firestore \
  PATH="$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin:$PATH" \
  FIREBASE_BIN="$HOME/.cache/clandexis-tools/fb/node_modules/.bin/firebase" \
  tests/run_ledger_tests.sh
```

An emulator-dependent skip means that integration check did not run.

## Database transfer test

The isolated PostgreSQL test database must be running. With Java and Firebase CLI available:

```bash
DATABASE_BACKEND=firestore \
  DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_app_test' \
  firebase emulators:exec --only firestore --project demo-clandexis \
  'PYTHONPATH=. .venv/bin/python -m pytest tests/postgres_migration_checks.py -v'
```

The test copies a committed sale and repayment, preserves a pending conversation, compares stock and receivables, reruns the import, and confirms that the source financial records remain unchanged.

## Build and dependency checks

```bash
(cd dashboard && npm run build)
.venv/bin/python -m pip check
git diff --check
```

## Live Gemini smoke test

The running local API was exercised with the configured Gemini credential and PostgreSQL. The Romanized Telugu sale lacked price/payment details. Two answers updated the same candidate. Sales remained zero until explicit confirmation.

| State | Sales | Cumulative UPI | Outstanding | Shirts |
| --- | --- | --- | --- | --- |
| Fresh seed | ₹0 | ₹0 | ₹0 | 200 |
| Sale committed | ₹10,000 | ₹2,000 | ₹8,000 | 150 |
| Duplicate confirmation | ₹10,000 | ₹2,000 | ₹8,000 | 150 |
| ₹3,000 repayment committed | ₹10,000 | ₹5,000 | ₹5,000 | 150 |

Invoice download returned a PDF with original total ₹10,000, paid ₹2,000, and due ₹8,000. A PDF-ledger request staged no candidate. The actual `../voice-telugu.mp3` file was uploaded with staging disabled and reached Gemini. Model output still requires review before any later posting.

The final restart exposed a launcher import-order bug. It was corrected, covered by a separate-process regression test, and rechecked with HTTP 200 login, PostgreSQL health metadata, and preserved totals. The dashboard login page was also opened in Chrome. These checks used local HTTP APIs and did not deliver messages through Meta.
