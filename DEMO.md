# Clandexis buildathon demo

Clandexis is a multilingual, multimodal ERP for small merchants. It converts text, Telugu/Hindi voice, receipt images, and PDFs into Schema 1.1 transaction candidates. Nothing reaches the ledger until the authenticated merchant reviews and confirms the exact persisted candidate.

## Verified release

- Source branch: `test`
- Application commit: `189d538cb6827fe39cdcf9d388af7373f799ee0e`
- Persistence: PostgreSQL 17 with row-level security
- PostgreSQL signed-webhook E2E: 10/10 passed
- General tests: 92 passed; 4 legacy Firestore-emulator modules skipped
- Live Gemini audit: 10/10 text/audio/image/PDF inputs completed
- Frontend production build: passed
- Live Cloud Run, Cloud SQL, GCS, and corrected real-phone delivery: not yet verified

## Architecture

```text
Dashboard or signed Meta webhook
  -> approved phone-to-merchant identity
  -> Gemini Schema 1.1 extraction
  -> deterministic validation and clarification
  -> persisted PostgreSQL candidate
  -> candidate-specific Confirm/Cancel
  -> atomic PostgreSQL ledger, inventory, receivable and journal commit
  -> invoice generated from the committed sale
```

Greetings, stock, customer balances, and daily summaries use the same merchant-scoped PostgreSQL state as the dashboard. Unknown senders and cross-merchant requests are rejected. Query and report requests never create financial transactions.

## Start the prepared local demo

From the repository root:

```bash
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_demo' \
  PYTHONPATH=. .venv/bin/python scripts/run_postgres_demo.py
```

Open <http://127.0.0.1:8180/dashboard/> and sign in as `demo`. Copy the protected local password to the clipboard without printing it:

```bash
.venv/bin/python -c 'import json,pathlib,subprocess; p=pathlib.Path.home()/".local/state/clandexis-postgres-demo/session.json"; subprocess.run(["pbcopy"],input=json.loads(p.read_text())["password"].encode(),check=True)'
```

The launcher disables Meta transport. Local PostgreSQL data and credentials remain under `~/.local/state/clandexis-postgres-demo/`.

## Reproducible sale and repayment

The current demo already contains the verified result: ₹10,000 sales, ₹5,000 cumulative UPI, ₹5,000 outstanding for Suresh Traders, and 150 Cotton Shirts. Repeating a new sale will add another transaction.

For a freshly seeded test merchant, start with 200 Cotton Shirts and submit:

> Nenu Suresh Traders ki eeroju 50 Cotton Shirt (L) ammanu.

Answer the same candidate's questions:

> Okkokka Cotton Shirt (L) dhara 200 rupees.

> 2000 rupees UPI lo vachayi, migilina 8000 rupees udhaar.

Review the full ₹10,000 / ₹2,000 / ₹8,000 summary and click **Confirm**. Expected state:

| Measure | After sale |
| --- | ---: |
| Sales | ₹10,000 |
| UPI | ₹2,000 |
| Suresh outstanding | ₹8,000 |
| Cotton Shirts | 150 |

Then submit and confirm:

> Suresh Traders paid INR 3000 through UPI against his previous outstanding balance. No new sale or stock movement.

Expected final state: sales ₹10,000, cumulative UPI ₹5,000, outstanding ₹5,000, stock 150. Reconfirming the original candidate must not create another transaction or stock deduction.

## Multilingual and media checks

- Hindi missing-details input: `आज मैंने राजू को 10 कॉटन शर्ट बेचीं।`
- Romanized Hindi: `Maine aaj Suresh ko 10 cotton shirts bechi.`
- Code-switched repayment: `Suresh ne previous udhaar ke against 3000 UPI pay chesadu.`
- Telugu audio fixture: `../voice-telugu.mp3`
- PDF fixture: `../Invoice 1.pdf`

Use **Live Extractor** for files. Review transcript, buyer, item, quantity, price, payment, validation, and candidate ID before confirmation. A non-transaction image must return `unknown` and create no ledger write. A mismatched invoice must remain in review.

## Invoice and backup

After confirming a sale, open **Transactions** and download its invoice. The PDF is generated from immutable committed transaction data. A generation failure leaves the sale committed and reports a retryable document status.

The prepared browser-demo database backup is:

```text
~/.local/state/clandexis-postgres-demo/release-189d538.dump
```

It is a validated PostgreSQL custom-format archive with mode `0600`. Do not restore it over an existing database; restore into a separate empty demo database.

## Known limitations and release safety

- Catalog and available-stock eligibility are finally enforced during confirmation. A candidate can display Confirm and then be held safely if its item is unknown or stock is insufficient.
- Gemini transcription can be uncertain; explicit review and confirmation remain mandatory.
- The old Cloud Run callback is not a trusted release. Meta must stay unsubscribed until a new revision is deployed from the recorded SHA, connected to an isolated Cloud SQL database, and passes staging verification.
- Cloud SQL credentials, Secret Manager bindings, GCS invoice delivery, and real Meta delivery require direct cloud evidence before being claimed.

See [testing evidence](docs/TESTING.md), [PostgreSQL setup](docs/POSTGRES_MIGRATION.md), and [deployment requirements](docs/DASHBOARD_DEPLOYMENT.md).
