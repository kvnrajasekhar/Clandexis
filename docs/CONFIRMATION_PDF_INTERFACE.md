# Confirmation and invoice contracts

The application boundary is `app/services/confirmation.py`. Backend selection is in `app/services/persistence.py`. Both FastAPI entry points initialize the selected adapter during lifespan, after runtime configuration is loaded.

| Backend | Confirmation adapter | Invoice repository |
| --- | --- | --- |
| PostgreSQL | `PostgresLedgerAdapter` | `PostgresInvoiceRepository` |
| Firestore | `ClaudeLedgerAdapter` | `FirestoreInvoiceRepository` |

## Candidate lifecycle

1. Gemini emits Schema 1.1 candidates.
2. The pipeline and ledger apply deterministic validation.
3. `stage_candidate` returns the persisted candidate ID. Repeated source events retain that ID.
4. Incomplete candidates remain `NEEDS_REVIEW`. Clarification updates the same candidate and revalidates it.
5. A valid candidate becomes `PENDING_CONFIRMATION` and receives its explicit actions.
6. Confirm calls the selected backend's `confirm_and_commit`; the candidate becomes `POSTED`, and its transaction is `COMMITTED`.
7. Cancel creates no financial posting. Rejected and expired candidates cannot post.

The application confirmation window is 30 minutes. Use the stored expiry for decisions. Treat revisions as opaque values returned by the database; PostgreSQL and Firestore use different snapshot serialization for hashes.

## Application interface

```python
from app.services.confirmation import AuthenticatedMerchant, handle_confirmation

# Construct this identity after verified session/sender authorization.
merchant = AuthenticatedMerchant(authorized_merchant_id, actor_phone=authorized_phone)
result = handle_confirmation(
    authenticated_merchant=merchant,
    candidate_id=persisted_candidate_id,
    action="confirm",
    source_event_id=unique_confirmation_event_id,
)
```

Confirm returns `PostingResult`:

| Field | Meaning |
| --- | --- |
| `transaction_id` | Actual committed transaction ID |
| `amount_paid` | Actual received amount for this posting |
| `credit_outstanding` | Party balance at posting when available; otherwise the transaction credit |
| `inventory_balance` | Affected product balances at posting |
| `posting_status` | `COMMITTED` |

Cancel returns `CANCELLED`. Decimal amounts come from persisted results. A replay reflects its original posting snapshot; read the dashboard/party view for current balances after later transactions.

## WhatsApp actions

Buttons encode the persisted candidate ID:

```text
clx:v1:confirm:<candidate_id>
clx:v1:cancel:<candidate_id>
```

The adapter extracts real `interactive.button_reply.id` and `interactive.list_reply.id`. Malformed IDs fail before extraction. The WhatsApp application rejects bare YES/NO and withholds Confirm while clarification is required. Merchant scope, expiry, revision, and validation gates are checked again before commit.

The bridge depends on the ledger operations below. Each operation is scoped to the authorized merchant:

- Stage and read a persisted candidate.
- Replace a reviewed candidate under its expected revision.
- Confirm and commit atomically under the callback request ID.
- Cancel a candidate.
- Read the committed transaction and its recorded movements/results.

The SQL adapter reuses canonical planning from `app/services/ledger.py`. [The Firestore reference](LEDGER_INTERFACE.md) documents that backend's detailed method signatures. PostgreSQL setup and transfer behavior are in [the migration guide](POSTGRES_MIGRATION.md).

## Dashboard API

All routes below require the verified session. Mutations also require `X-CSRF-Token`.

| Method and path | Purpose |
| --- | --- |
| `POST /api/v1/extract/text` | Extract/validate; `stage=true` persists eligible candidates |
| `POST /api/v1/extract/media` | Multipart media extraction with the same staging option |
| `GET /api/v1/dashboard/candidates` | Candidate IDs, revisions, statuses, and facts |
| `POST /api/v1/dashboard/candidates/{id}/clarify` | Apply an answer using `answer` and the current `revision` |
| `POST /api/v1/dashboard/candidates/{id}/review` | Explicit corrected candidate and review note |
| `POST /api/v1/dashboard/candidates/confirm` | Confirm the supplied persisted `candidate_id` |
| `POST /api/v1/dashboard/candidates/cancel` | Cancel the supplied persisted `candidate_id` |
| `GET /api/v1/dashboard/transactions/{id}/invoice` | Authenticated invoice download or signed-URL redirect |

A confirmation request uses this shape after the displayed candidate has been reviewed:

```json
{
  "candidate_id": "PERSISTED_CANDIDATE_ID",
  "source_event_id": "dashboard:UNIQUE_ACTION_ID"
}
```

Legacy merchant/actor fields are compatibility checks. Authorization comes from the signed session. [Security configuration](P0_APPLICATION_INTEGRATION.md) includes a login example.

## Committed-sale PDF interface

```python
from pdf_service import InvoiceService

artifact = InvoiceService(repository, artifact_store).generate_document(
    authenticated_merchant, committed_transaction_id
)
```

The repository supplies `CommittedSale` from database records. It contains merchant/buyer names, invoice number/date/currency, items, quantities, prices, subtotal, discounts, tax, total, paid, and due. Monetary reconciliation runs before rendering. PostgreSQL invoice facts are captured with the sale; artifact generation follows the ledger commit.

`InvoiceArtifact` associates merchant ID, transaction ID, storage key, SHA-256, filename, and PDF content type. Artifacts use content-addressed storage and a retry-safe association. Local storage serves authenticated downloads. GCS storage uploads immutable content and can resolve signed document URLs for the sender.

The PDF is labeled a basic invoice. Implemented tax-rule verification and merchant tax details are prerequisites for claiming tax compliance.

## Failure and retry behavior

Generation or storage failure returns invoice status `PENDING_RETRY` while preserving the sale. Repeating authenticated confirmation with a new action event can replay the commit and retry document generation. Repeated delivery of the same WhatsApp event can return its cached reply without rendering again.

A repayment returns invoice status `NOT_APPLICABLE`. It updates receivables without creating another sale invoice or moving stock. Rendering, uploads, signed URL creation, and Meta calls run outside ledger transaction callbacks.

[TESTING.md](TESTING.md) records the verification coverage and service boundaries.
