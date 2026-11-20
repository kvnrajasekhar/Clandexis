# Confirmation bridge and committed-sale PDF interface

## Audit and scope

The implementation is in `team_clandexis`, the shared project. The parent `clandexis/app` is the earlier extractor project and was left untouched. The pipeline is `app/pipeline.py`; there is no `app/services/pipeline.py` in this checkout. `pdf_service/` was empty, despite the README listing a `pdf-service/` implementation.

Previously, `orchestrate_inbound_message` called `PipelineService.process_and_interpret`, selected the first extracted candidate, used the inbound message ID in `CONFIRM_...`/`CANCEL_...` buttons, and reported ACCEPTED candidates as recorded without posting anything. The Meta adapter already normalizes `interactive.button_reply.id` and `interactive.list_reply.id` into `InboundMessage.interactive_id`. That adapter and its credentials/delivery code are unchanged.

Now replies route before extraction. All new transaction candidates require confirmation. Buttons use the ID returned by durable staging, not the proposed ID, message ID, or original message text. Multiple extracted transactions ask for individual submissions instead of silently discarding all but the first. Unsupported query handling remains outside this bridge.

The extractor, validator, Firestore entity schema and dashboard are unchanged. `app/services/ledger.py` belongs to Claude and is consumed through an adapter; no ledger implementation was added here.

## Startup and authentication

The existing webhook lifespan configures:

```python
from app.services.confirmation import configure_confirmation
from app.services.confirmation_ledger_adapter import ClaudeLedgerAdapter

configure_confirmation(ClaudeLedgerAdapter())
```

This uses Claude's existing `get_db()` and application default Google credentials. Missing configuration fails closed; it never falls back to an in-memory ledger. Standalone consumers must configure once at startup too.

`AuthenticatedMerchant(merchant_id)` represents an identity already authenticated and authorized by the caller. It is not a login mechanism. Never construct it from an arbitrary request body's merchant field. The existing webhook verifies the Meta signature and its adapter resolves tenant identity from the sender phone; ledger confirmation independently checks that actor against the merchant. Any additional account/delegate mapping remains the webhook owner's responsibility. No unauthenticated confirmation HTTP route was added.

## Application API and real replies

```python
from app.services.confirmation import AuthenticatedMerchant, handle_confirmation

merchant = AuthenticatedMerchant(authorized_merchant_id)
result = handle_confirmation(
    authenticated_merchant=merchant,
    candidate_id=persisted_candidate_id,
    action="confirm",                    # exactly "confirm" or "cancel"
    source_event_id="wamid.callback-id",  # callback event, not original extraction ID
)
# PostingResult(transaction_id, amount_paid: Decimal,
#               credit_outstanding: Decimal, inventory_balance: dict[str, Decimal],
#               posting_status="COMMITTED")
# Cancel returns "CANCELLED"; it never invokes confirm_and_commit.
```

Button IDs use `clx:v1:confirm:<persisted_candidate_id>` and `clx:v1:cancel:<persisted_candidate_id>`. Candidate IDs accept only ASCII letters, digits, underscore and hyphen, up to 128 characters. Malformed/legacy IDs fail closed, never enter the extractor, and do not post. Real WhatsApp message content:

```json
{
  "from": "919876543210",
  "id": "wamid.callback-id",
  "timestamp": "1795132800",
  "type": "interactive",
  "interactive": {
    "type": "button_reply",
    "button_reply": {
      "id": "clx:v1:confirm:cand_REPLACE_WITH_PERSISTED_ID",
      "title": "Confirm"
    }
  }
}
```

The existing adapter parses this; `ConfirmationService.parse_reply` validates the ID. Button titles never authorize actions. Exact text `YES`/`NO` (case-insensitive) works only if the authenticated merchant has exactly one unexpired active persisted candidate. Zero or multiple candidates require buttons. Ordinary correction messages are not interpreted as an approval.

Staging requests a 30-minute expiry. Retry staging is idempotent on merchant, source event and candidate index and preserves the original persisted ID/expiry. Expired pending candidates fail. Repeated Confirm on a committed candidate returns its original transaction. Repeated Cancel remains cancelled. Confirm after Cancel and Cancel after commit fail. A callback reused for a different candidate must raise Claude's `IdempotencyConflictError`.

## Review gate and remaining Claude dependency

ACCEPTED candidates are revalidated on a copy with the existing `BusinessValidator` before commit. REJECTED candidates cannot confirm or be reviewed into approval: submit a new corrected candidate. NEEDS_REVIEW, missing fields, clarification flags and unresolved original review flags block posting. A normal Confirm tap does not constitute review.

Application review API:

```python
from app.services.confirmation import get_confirmation_service
from app.schemas.transaction import TransactionCandidate

corrected = TransactionCandidate.model_validate(merchant_verified_full_candidate)
reviewed = get_confirmation_service().resolve_review(
    merchant, persisted_candidate_id, corrected,
    review_note="Merchant verified buyer, quantities, payment and credit against source bill",
)
# Still unposted. A subsequent Confirm uses the persisted reviewed candidate.
```

The bridge clears old clarification/review flags on the supplied complete facts and runs the existing validator again; unresolved deterministic warnings or missing fields still block. This method works with the test interface stub. **Claude's current ledger has no `review_candidate` method**, so the production adapter fails closed with an explicit message to submit corrected details as a new candidate. It never directly overwrites a ledger snapshot. Required extension (owned by Claude):

```python
review_candidate(
    merchant_id: str,
    candidate_id: str,
    *,
    reviewed_by: str,
    corrected_candidate: TransactionCandidate,
    review_note: str,
    expected_snapshot_sha256: str,
    db=None,
    now=None,
)
```

Required atomic behavior: authorize reviewer, check tenant, pending state, expiry and expected hash; reject original REJECTED or terminal candidates; revalidate corrected facts; preserve the original snapshot/review provenance and append audit history; persist the reviewed snapshot with its new hash and `review_resolved=True`; retain immutable `originally_needs_review=True` when review was originally required; never post as part of review. Confirmation must recheck these gates atomically. Return value is not used: the adapter re-reads the persisted candidate.

## Exact existing ledger dependencies

`ClaudeLedgerAdapter` invokes these existing public functions:

| Function | Bridge arguments/use |
| --- | --- |
| `get_db()` | Shared Firestore client; no separate ledger store |
| `stage_candidate(merchant_id, source_message_id, candidate, *, candidate_index, ttl, db, now)` | Preserve returned `candidate_id`; no direct candidate writes |
| `confirm_and_commit(merchant_id, candidate_id, *, confirmed_by, confirmation_request_id, expected_snapshot_sha256, db, now)` | Callback ID is request ID; snapshot hash prevents stale confirmation |
| `cancel_candidate(merchant_id, candidate_id, *, cancelled_by, reason, db, now)` | Reason records callback ID; no financial posting |
| `get_transaction(merchant_id, txn_id, *, db)` | Read committed transaction and referenced movement IDs |
| `LedgerError` and subclasses | Translate known application failures into safe confirmation errors |

The adapter reads `merchants/{merchant_id}/transaction_candidates/{candidate_id}` with `extracted_snapshot`, `snapshot_sha256`, `expires_at`, `posting_status`, `validation_status`, and future review metadata. It maps `PENDING_CONFIRMATION` to `AWAITING_CONFIRMATION`, `POSTED` to `COMMITTED`, and retains terminal states. Existing NEEDS_REVIEW ledger holds remain blocked by the application gate.

`CommitResult` must contain matching merchant ID, `txn_id`, `status="COMMITTED"`, `received_amount`, and `credit_amount`. Paid/credit in the response come from this persisted commit result. Inventory balances come from the committed transaction's ordered `movement_ids`, reading each movement's `sku_id`, `reference_txn_id` and `balance_after`. This returns the balance at posting even on a later duplicate callback, rather than querying current stock. Credit outstanding means credit on this transaction; it is not the party's aggregate preexisting debt.

Atomic exactly-once posting, callback binding, authorization, hash checks, financial invariants, inventory updates and commit-result persistence remain Claude's ledger responsibilities. Application checks complement them and cannot replace them. No WhatsApp, PDF rendering, or object storage call occurs in a Firestore posting callback.

## Document API and webhook sender example

```python
from pdf_service import InvoiceService, LocalArtifactStore
from pdf_service.firestore_repository import FirestoreInvoiceRepository
from models.canonical import OutboundMessage, MessageType

invoices = InvoiceService(
    FirestoreInvoiceRepository(),
    LocalArtifactStore("/durable/invoices"),
)
artifact = invoices.generate_document(merchant, result.transaction_id)
# artifact: merchant_id, transaction_id, storage_key, sha256,
#           filename, content_type="application/pdf"

# Sender-owned storage/delivery integration:
# Publish the PDF at root / artifact.storage_key to durable object storage,
# or inject an ArtifactStore with put_if_absent(key, content) at construction.
# Resolve a fetchable HTTPS URL using the sender's existing storage mechanism.
outbound = OutboundMessage(
    recipient_phone=authorized_recipient_phone,
    message_type=MessageType.DOCUMENT,
    document_url=fetchable_https_pdf_url,
    document_filename=artifact.filename,
)
# await meta_service.dispatch_outbound(outbound) -- after every DB callback returns
```

This is an application interface, not a public HTTP endpoint. Automatic upload/WhatsApp PDF delivery remains the sender owner's integration; local filesystem paths are not valid Meta document URLs. A Cloud Run ephemeral filesystem is not durable storage: inject an object-store implementation or mount a durable volume. Do not change Meta tokens or credential logic to generate PDFs.

`InvoiceService` accepts only an authoritative `InvoiceRepository.get_committed_sale(merchant_id, transaction_id)` read model, revalidates it, verifies tenant/transaction ownership, and requires `COMMITTED` + `SALE_INVOICE`. It does not accept an extraction request body. The included Firestore repository reads Claude's committed transaction plus the merchant's database record. Missing/inconsistent database amounts fail instead of being invented.

The invoice includes merchant/buyer, stable invoice number (committed invoice number or transaction ID), business date, currency, wrapped item descriptions, quantities/units, prices, per-line discounts, subtotal, invoice discount, recorded tax, total, paid and due. Paid/credit/total/tax use committed integer minor units. Subtotal uses committed line amounts; the displayed invoice discount is the difference reconciled against committed total and tax. No tax rate or GST rule is inferred. Merchant details are current database details; immutable merchant/buyer address snapshots are a future ledger enhancement.

Claude's ledger v1.0 explicitly denominates its amounts in INR, so its adapter uses INR when that version lacks a currency field. Other versions must provide committed currency. The PDF read model also supports USD; **the current INR ledger cannot represent a USD sale merely because extraction said USD**. Cross-currency ledger posting remains outside this change.

All output is explicitly **BASIC INVOICE**, with “Not a GST tax invoice. No GST compliance certification.” Recorded tax can be displayed without claiming tax compliance. The service does not implement GST calculations, certification or KMS signing.

## Artifact retry and failure behavior

PDF bytes use ReportLab invariant mode. Artifact keys combine a hash of merchant/transaction identity and PDF content SHA-256. Local writes use a temporary file, fsync and an atomic no-overwrite link; retries cannot expose partial files. Production ArtifactStore implementations must preserve immutable put-if-absent semantics.

After storage succeeds, the Firestore repository separately associates one `InvoiceArtifact` at:

`merchants/{merchant_id}/transactions/{transaction_id}/invoice_artifacts/basic_v1`

The association transaction reads the committed sale and artifact first, creates only artifact metadata if absent, and returns the original metadata on retry/concurrent generation. It does not rewrite sale, stock, party, payment or journal records. Sale posting and document generation are separate operations: render/storage/association failure leaves the sale committed and can be retried by transaction ID. A failure after storage may leave an unassociated immutable file; retry recovers it. Concurrent changed merchant details may create an unused content-addressed file, but only one artifact is associated. Object cleanup and delivery retries belong to the worker/storage owner.

## Verification

Run from `team_clandexis`:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Validated on 2026-11-20: **50 passed, 1 skipped**. The skipped suite belongs to the ledger owner and requires `FIRESTORE_EMULATOR_HOST`. Existing pytest-asyncio emits a fixture-scope deprecation warning.

Coverage includes one commit, duplicate/concurrent callbacks, cancellation, tenant rejection, expiry, invalid IDs, safe YES/NO ambiguity, review/correction/revalidation, persisted ID reuse, real Meta adapter normalization without extraction, actual Claude API argument mapping, committed movement balances, committed minor-unit PDF totals, ownership/state/amount failures, repeatable artifact bytes, storage and association failures without undoing a sale. Stub concurrency tests verify bridge behavior against the atomic contract; they are not evidence of real Firestore ACID behavior. Run Claude's emulator suite before production rollout.

Synthetic fixture previews (not real customer invoices) were generated in `output/pdf/committed-basic-invoice-inr.pdf` and `output/pdf/committed-basic-invoice-usd.pdf`: 32 items, long merchant/buyer names, five pages each. All ten pages were rendered with Poppler and visually inspected for wrapping, row breaks, repeated headers, totals and footers. ReportLab's [table documentation](https://docs.reportlab.com/reportlab/userguide/ch7_tables/) informs the repeating/splitting table layout.
