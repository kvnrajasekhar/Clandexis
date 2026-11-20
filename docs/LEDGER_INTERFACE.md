# Ledger & Inventory Interface

Module: `app/services/ledger.py` (Firestore, merchant-scoped). Implements the frozen integration contract.
Every function takes an optional `db=` (a `google.cloud.firestore.Client`); default is `get_db()`, which honors
`FIRESTORE_EMULATOR_HOST`. All functions are synchronous.

```python
from app.services.ledger import (stage_candidate, confirm_and_commit, cancel_candidate, get_transaction,
    list_transactions, get_party_balance, get_inventory, get_daily_summary)
```

## Lifecycle

`TransactionCandidate` (Schema 1.1, from `app/schemas/transaction.py`) → `stage_candidate` → candidate doc
`posting_status`:

| posting_status | meaning |
|---|---|
| `PENDING_CONFIRMATION` | validated, awaiting an explicit merchant confirmation (nothing posted) |
| `NEEDS_REVIEW` | held: extractor clarification outstanding (`hold_source=EXTRACTION`, not confirmable) or ledger hold, e.g. stock/ambiguity (`hold_source=LEDGER`, confirmable again after the cause is fixed) |
| `REJECTED` | arithmetically/semantically invalid (terminal) |
| `CANCELLED` | merchant cancelled (terminal) |
| `POSTED` | committed; `committed_txn_id` set |
| `FAILED` | reserved for the orchestrator to mark infrastructure failures; the ledger never sets it |

Committed transactions carry `status="COMMITTED"` (existing `TransactionStatus`). `ACCEPTED` validation is never
posting by itself; **only `confirm_and_commit` posts**. Model confidence never permits posting.

## Methods

### `stage_candidate(merchant_id, source_message_id, candidate, *, candidate_index=0, source_channel="meta_whatsapp", source_media_type="text", ttl=24h, db=None, now=None) -> StagedCandidate`
- `candidate`: `TransactionCandidate` or dict. `source_message_id`: stable WhatsApp message id (required).
  `candidate_index`: position when one message yields several transactions.
- Verifies the merchant exists. Re-runs `BusinessValidator` server-side; stricter of supplied vs recomputed
  `validation_status` wins (client `ACCEPTED` is never trusted).
- Idempotent on `(merchant_id, source_message_id, candidate_index)`: repeats return the existing candidate with
  `already_staged=True`; the snapshot is never overwritten.
- Returns `StagedCandidate{candidate_id, merchant_id, source_message_id, validation_status, posting_status, hold_reasons, validation_warnings, already_staged, expires_at}`.
- Errors: `MerchantNotFoundError`, `ValueError` (blank ids), pydantic `ValidationError` (malformed candidate).

### `confirm_and_commit(merchant_id, candidate_id, *, confirmed_by, confirmation_request_id=None, expected_snapshot_sha256=None, db=None, now=None) -> CommitResult`
- `confirmed_by`: sender phone from the webhook. Must equal `merchant_id` or be in the merchant doc's
  `authorized_phones` (digits-only comparison, so Meta's `9198…` matches `+9198…`).
- `confirmation_request_id`: stable id of the tap (e.g. `CONFIRM_<message_id>`); default `confirm:<candidate_id>`.
- `expected_snapshot_sha256` (optional): `snapshot_sha256` shown to the user; mismatch ⇒ `StaleConfirmationError`.
- One Firestore transaction: reads (merchant, candidate, confirmation, party, SKUs, accounts) then writes. Re-derives
  every amount from the snapshot in `Decimal`, resolves customer/SKU by exact normalized name/alias (never creates
  entities), re-checks stock, credit limit and outstanding balance.
- Writes: `transactions`, `payments` (actual receipts only), `inventory_movements`, `inventory` stock, `parties`
  balance, `journal_entries`, `accounts`, candidate → `POSTED`, `confirmations` idempotency record.
  - `sale`: stock −qty; unpaid remainder → party receivable; Dr CASH/UPI/BANK + Dr AR, Cr SALES_REVENUE (+Cr GST_PAYABLE).
  - `payment_received`: party balance −amount (cannot exceed outstanding); Dr CASH/UPI/BANK, Cr AR. No sale, no stock.
  - `stock_added`: stock +qty, `PRODUCTION_ADD` movements, no revenue, no journal. SKU must already exist.
  - `purchase`, `payment_made`, `stock_adjustment`, `query`, `unknown` are **not supported** → staged as `NEEDS_REVIEW`.
- Returns `CommitResult{candidate_id, txn_id, merchant_id, intent, status, total_amount, received_amount, credit_amount, party_id, party_balance, idempotent_replay}` (money is `Decimal`).
  Replays (same request id, or any confirmation of an already `POSTED` candidate) return the original result with
  `idempotent_replay=True` and write nothing.
- Errors:
  | error | when | state change |
  |---|---|---|
  | `MerchantNotFoundError` | unknown merchant | none |
  | `NotAuthorizedError` | `confirmed_by` not authorised | none |
  | `CandidateNotFoundError` | not found / other tenant | none |
  | `InvalidStateError` | candidate CANCELLED/REJECTED/EXTRACTION-held; snapshot hash mismatch | none |
  | `StaleConfirmationError` | past `expires_at`, or `expected_snapshot_sha256` mismatch | none |
  | `IdempotencyConflictError` | request id already used for another candidate | none |
  | `HeldForReviewError(.reasons)` | unknown/ambiguous customer or SKU, unit mismatch, insufficient stock, credit limit, payment > outstanding, missing method, unsupported type | candidate → `NEEDS_REVIEW` (LEDGER hold); nothing posted |
  | `ValidationFailedError(.reasons)` | revalidation finds invalid arithmetic | candidate → `REJECTED`; nothing posted |
  | `LedgerImbalanceError` | journal would not balance (bug guard) | none (transaction aborted) |

  Any other exception raised during commit leaves **no** partial state (Firestore transaction aborts).

### `cancel_candidate(merchant_id, candidate_id, *, cancelled_by, reason=None, db=None, now=None) -> CandidateStatus`
`PENDING_CONFIRMATION`/`NEEDS_REVIEW` → `CANCELLED` (idempotent). `POSTED` ⇒ `InvalidStateError` (committed records are
never changed). Errors: `NotAuthorizedError`, `CandidateNotFoundError`, `InvalidStateError`.

### `get_transaction(merchant_id, txn_id, *, db=None) -> dict`
Committed transaction document. Raises `TransactionNotFoundError`.

### `list_transactions(merchant_id, *, intent=None, party_id=None, business_date=None, limit=50, db=None) -> List[dict]`
Newest first. `business_date` is `YYYY-MM-DD` (IST). `intent` is a `TransactionIntent`. Intent/party filters run
in-process (no composite indexes needed); fine at MVP volumes.

### `get_party_balance(merchant_id, party_id=None, *, party_name=None, db=None) -> PartyBalance`
`PartyBalance{party_id, display_name, outstanding, credit_limit, available_credit, last_txn_at}`. `party_name` uses
exact normalized match on display name/alias/slug; unknown or ambiguous ⇒ `PartyNotFoundError`.

### `get_inventory(merchant_id, sku_id=None, *, db=None) -> List[InventoryStock]`
`InventoryStock{sku_id, item_name, unit, current_stock, default_unit_price, reorder_level, below_reorder_level}`.
Unknown `sku_id` ⇒ `InventoryNotFoundError`.

### `get_daily_summary(merchant_id, business_date=None, *, db=None) -> DailySummary`
IST day (default today): `transaction_count, sales_count, sales_total, cash_received, upi_received, bank_received,
credit_extended, collections_total, units_produced, total_receivables_outstanding`. Everything is computed from
committed documents.

## Firestore collections (all under `merchants/{merchant_id}/`)

| collection | doc id | notes |
|---|---|---|
| *(root)* `merchants/{id}` | phone | existing `Merchant`; optional `authorized_phones: [str]` |
| `parties` | slug | existing `Party`. Ledger keeps `current_udhaar_balance` (float, legacy) **and** `udhaar_balance_minor` (exact paise, preferred on read; seed docs without it still work) |
| `inventory` | sku | existing `InventoryItem`; `current_stock` updated only by the ledger |
| `inventory_movements` | `mv_<txn>_<line>` | append-only `InventoryMovement` (+`line_index`) |
| `transaction_candidates` | `cand_<sha256(merchant,message,index)>` | immutable `extracted_snapshot` + `snapshot_sha256`, `validation_status`, `posting_status`, `created_at`, `confirmed_at`, `expires_at`, `status_history[]`, `commit_result` |
| `transactions` | `txn_<same hash>` | existing `Transaction` model + `candidate_id, source_message_id, confirmation_request_id, confirmed_by, business_date, total_minor, received_minor, credit_minor, tax_minor, journal_id, movement_ids`. Created with `create()` ⇒ unique |
| `payments` | `pay_<txn>_<i>` | actual receipts only (CASH/UPI/BANK_TRANSFER); never credit |
| `journal_entries` | `jrnl_<txn>` | `lines[{account, debit_minor, credit_minor}]`, balanced |
| `accounts` | CASH, UPI, BANK, ACCOUNTS_RECEIVABLE, SALES_REVENUE, GST_PAYABLE | running `debit_total_minor`, `credit_total_minor`, `balance_minor` (debit − credit; revenue/liabilities are negative) |
| `confirmations` | `conf_<sha256(merchant,request_id)>` | idempotency record for confirmation requests |

Money is computed with `Decimal` (2 dp, `ROUND_HALF_UP`, floats converted through `str`), quantities at 3 dp, and
journals/payments/transactions also store exact integer paise (`*_minor`). Float fields remain only for compatibility
with existing models.

## Running the tests

```
tests/run_ledger_tests.sh        # needs Java 21+ and firebase-tools; starts the real Firestore emulator
```
Without `FIRESTORE_EMULATOR_HOST` the test module is skipped (not passed).
