# Local PostgreSQL demo

Open **http://127.0.0.1:8180/dashboard/**. Sign in as **demo**.

On the prepared Mac, copy the generated password to the clipboard without printing it, then paste it into the login form:

```bash
.venv/bin/python -c 'import json,pathlib,subprocess; p=pathlib.Path.home()/".local/state/clandexis-postgres-demo/session.json"; subprocess.run(["pbcopy"],input=json.loads(p.read_text())["password"].encode(),check=True)'
```

## Start or restart

Run from the repository root:

```bash
DATABASE_URL='postgresql://clandexis_app_test@127.0.0.1:55439/clandexis_demo' \
  PYTHONPATH=. .venv/bin/python scripts/run_postgres_demo.py
```

If an instance already owns port 8180, stop that instance with Ctrl-C before starting another. The launcher retains its merchant, credentials, and financial records. PostgreSQL data is stored under `~/.local/state/clandexis-postgres-demo/pgdata`. The protected session file is in the same directory.

The approved phone comes from `POSTGRES_DEMO_PHONE` or the sole entry in the ignored `WHATSAPP_DEMO_PHONE_MAP`. The launcher applies the PostgreSQL and session settings before loading the application. Meta transport is disabled for this local demo.

[PostgreSQL setup](docs/POSTGRES_MIGRATION.md) covers a fresh machine and separate test databases.

## Starting data and existing results

A newly seeded account has 200 Cotton Shirt (L), 150 Linen Kurta (M), and zero customer balances. Seeded parties include Suresh Traders, Ramesh Textiles, and Raju.

The current prepared demo has already completed the verified sale and repayment below. It shows ₹10,000 sales, ₹5,000 cumulative UPI, ₹5,000 outstanding for Suresh Traders, and 150 shirts. Repeating a new sale will add another sale. Reusing an existing candidate's confirmation has one posting effect.

For repeatable clean scenarios, use the isolated accounts created by [the test suite](docs/TESTING.md). Keep the current demo records when inspecting the completed flow.

## Sale with clarification

1. Open **Live Extractor** and enter the incomplete sale. Click **Run Extraction & Validation**.
2. Check the returned candidate ID and `NEEDS_REVIEW` status. Open **Overview** to find the candidate's **Reply to clarify** field.
3. Answer the price question, then the payment question. Click **Send reply** after each answer. Both answers update the same candidate.
4. Once it becomes `ACCEPTED / PENDING_CONFIRMATION`, check buyer, items, quantities, total, paid, and credit. Click **Authorize & Commit to Ledger** in Overview, or **Commit** for the same ID in **Candidates**.
5. Check **Transactions**, **Inventory**, and **Overview**. Open **Download invoice** on the committed sale.

**Incomplete Romanized Telugu input**

> Nenu Suresh Traders ki eeroju 50 Cotton Shirt (L) ammanu.

**Price answer**

> Okkokka Cotton Shirt (L) dhara 200 rupees.

**Payment answer**

> 2000 rupees UPI lo vachayi, migilina 8000 rupees udhaar.

**Complete English alternative**

> Sold 50 Cotton Shirt (L) to Suresh Traders at INR 200 each, total INR 10000. Received INR 2000 by UPI. Remaining INR 8000 on credit.

**Hindi input for a missing-details check**

> आज मैंने राजू को 10 कॉटन शर्ट बेचीं।

A price/payment follow-up is expected. Review the actual extraction before proceeding.

## Repayment

In **Live Extractor**, submit:

> Suresh Traders paid INR 3000 through UPI against his previous outstanding balance. No new sale or stock movement.

Review and confirm its persisted candidate. From a freshly seeded account that has completed the sale above, the results are:

| Measure | After sale | After repayment |
| --- | --- | --- |
| Sales | ₹10,000 | ₹10,000 |
| Cumulative UPI | ₹2,000 | ₹5,000 |
| Customer outstanding | ₹8,000 | ₹5,000 |
| Shirts | 150 | 150 |

The sale invoice retains total ₹10,000, paid ₹2,000, and due ₹8,000 as recorded at posting. The customer's current outstanding appears in Overview and the party balance.

## Audio and invoice downloads

Select **Upload audio** in Live Extractor. A selected file takes precedence over typed text. Use a real recording with the transaction details and inspect the transcript/extraction before confirmation. The sample `../voice-telugu.mp3` was uploaded to live Gemini with staging disabled during verification; its content is independent of the scripted sale above.

Download the PDF from **Transactions** after committing a sale. Invoice storage failures leave the sale committed and report `PENDING_RETRY`.

The personal Firestore emulator from the earlier demo is currently unavailable. Its preserved archives and the current SQL demo are described in [the migration guide](docs/POSTGRES_MIGRATION.md).
