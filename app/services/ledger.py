"""
Atomic ledger & inventory service for Clandexis Invisible ERP (Firestore).

Public interface (frozen integration contract):
    stage_candidate(...)        -> StagedCandidate
    confirm_and_commit(...)     -> CommitResult
    cancel_candidate(...)       -> CandidateStatus
    get_transaction(...)        -> dict
    list_transactions(...)      -> List[dict]
    get_party_balance(...)      -> PartyBalance
    get_inventory(...)          -> List[InventoryStock]
    get_daily_summary(...)      -> DailySummary

Design rules (see docs/LEDGER_INTERFACE.md):
  * Every document lives under ``merchants/{merchant_id}/...``. merchant_id is only ever
    used to build a path, so a caller can never reach another tenant's documents.
  * Extraction output is staged as an immutable snapshot. Nothing is posted until an
    authorised merchant confirms; model confidence alone never permits posting.
  * The commit re-derives every amount from the snapshot with Decimal arithmetic and
    re-checks stock/receivables inside one Firestore transaction. The transaction
    callback is pure: it performs Firestore reads, then writes, and nothing else
    (no Gemini, WhatsApp or PDF side effects).
  * Credit/udhaar is an outstanding receivable (party balance + AR account), never a
    received payment.
  * Committed records are append-only. Nothing here deletes a committed document.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Aborted, AlreadyExists
from google.cloud import firestore
from pydantic import BaseModel, Field

from app.schemas.transaction import PaymentStatus as ExtractedPaymentStatus
from app.schemas.transaction import TransactionCandidate, TransactionType
from app.services.validator import BusinessValidator
from config import settings
from models.firestore_schema import (
    InventoryItem,
    LineItem,
    MovementType,
    Party,
    PaymentMethod,
    PaymentNode,
    PaymentStatus,
    Transaction,
    TransactionIntent,
    TransactionStatus,
)

logger = logging.getLogger("clandexis.ledger")

LEDGER_VERSION = "1.0"
IST = ZoneInfo("Asia/Kolkata")
CANDIDATE_TTL = timedelta(hours=24)

_MONEY_Q = Decimal("0.01")
_QTY_Q = Decimal("0.001")

# Collection names (all under merchants/{merchant_id}/)
COL_MERCHANTS = "merchants"
COL_PARTIES = "parties"
COL_INVENTORY = "inventory"
COL_MOVEMENTS = "inventory_movements"
COL_TRANSACTIONS = "transactions"
COL_CANDIDATES = "transaction_candidates"
COL_CONFIRMATIONS = "confirmations"
COL_PAYMENTS = "payments"
COL_JOURNAL = "journal_entries"
COL_ACCOUNTS = "accounts"
COL_OPENING = "opening_balances"

# Chart of accounts (debit-normal: assets; credit-normal: revenue / liabilities)
ACC_CASH, ACC_UPI, ACC_BANK = "CASH", "UPI", "BANK"
ACC_AR, ACC_SALES, ACC_GST = "ACCOUNTS_RECEIVABLE", "SALES_REVENUE", "GST_PAYABLE"
ACC_OPENING = "OPENING_BALANCE_EQUITY"

_METHOD_ACCOUNT = {
    "cash": ACC_CASH,
    "upi": ACC_UPI,
    "online": ACC_UPI,
    "bank_transfer": ACC_BANK,
    "bank": ACC_BANK,
    "cheque": ACC_BANK,
    "card": ACC_BANK,
}
_ACCOUNT_METHOD = {
    ACC_CASH: PaymentMethod.CASH,
    ACC_UPI: PaymentMethod.UPI,
    ACC_BANK: PaymentMethod.BANK_TRANSFER,
}
_UNIT_SYNONYMS = {
    "piece": "pieces", "pieces": "pieces", "pcs": "pieces", "pc": "pieces",
    "nos": "pieces", "unit": "pieces", "units": "pieces", "number": "pieces",
    "meter": "meters", "meters": "meters", "metre": "meters", "metres": "meters", "mtr": "meters", "m": "meters",
}


# ==============================================================================
# Errors
# ==============================================================================

class LedgerError(Exception):
    """Base class for all ledger errors."""


class MerchantNotFoundError(LedgerError): ...
class NotAuthorizedError(LedgerError): ...
class CandidateNotFoundError(LedgerError): ...
class TransactionNotFoundError(LedgerError): ...
class PartyNotFoundError(LedgerError): ...
class InventoryNotFoundError(LedgerError): ...
class InvalidStateError(LedgerError): ...
class StaleConfirmationError(LedgerError): ...
class IdempotencyConflictError(LedgerError): ...
class LedgerImbalanceError(LedgerError): ...


class HeldForReviewError(LedgerError):
    """Candidate cannot be posted automatically (ambiguity, stock, credit limit...).
    The candidate is left in NEEDS_REVIEW with the reasons recorded. Nothing was posted."""

    def __init__(self, reasons: List[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(reasons))


class ValidationFailedError(LedgerError):
    """Server-side revalidation rejected the candidate. It is marked REJECTED."""

    def __init__(self, reasons: List[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(reasons))


# ==============================================================================
# Public return models
# ==============================================================================

class PostingStatus(str, Enum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    POSTED = "POSTED"
    FAILED = "FAILED"


class StagedCandidate(BaseModel):
    candidate_id: str
    merchant_id: str
    source_message_id: str
    validation_status: str
    posting_status: PostingStatus
    hold_reasons: List[str] = Field(default_factory=list)
    validation_warnings: List[str] = Field(default_factory=list)
    already_staged: bool = False
    expires_at: Optional[datetime] = None


class CandidateStatus(BaseModel):
    candidate_id: str
    merchant_id: str
    posting_status: PostingStatus
    committed_txn_id: Optional[str] = None


class CommitResult(BaseModel):
    candidate_id: str
    txn_id: str
    merchant_id: str
    intent: str
    status: str
    total_amount: Decimal
    received_amount: Decimal
    credit_amount: Decimal
    party_id: Optional[str] = None
    party_balance: Optional[Decimal] = None
    idempotent_replay: bool = False


class PartyBalance(BaseModel):
    party_id: str
    display_name: str
    outstanding: Decimal
    credit_limit: Decimal
    available_credit: Decimal
    last_txn_at: Optional[datetime] = None


class InventoryStock(BaseModel):
    sku_id: str
    item_name: str
    unit: str
    current_stock: Decimal
    default_unit_price: Decimal
    reorder_level: Decimal
    below_reorder_level: bool


class OpeningBalanceResult(BaseModel):
    party_id: str
    amount: Decimal
    journal_id: Optional[str] = None
    already_posted: bool = False


class ReceivablesReconciliation(BaseModel):
    merchant_id: str
    ar_account_balance: Decimal       # ACCOUNTS_RECEIVABLE control account
    journal_backed_party_total: Decimal
    party_balance_total: Decimal      # sum of parties.current_udhaar_balance
    unposted_opening_total: Decimal   # party balance with no journal posting behind it
    consistent: bool                  # control account == journal-backed party total


class DailySummary(BaseModel):
    merchant_id: str
    business_date: str
    transaction_count: int
    sales_count: int
    sales_total: Decimal
    cash_received: Decimal
    upi_received: Decimal
    bank_received: Decimal
    credit_extended: Decimal
    collections_total: Decimal
    units_produced: Decimal
    total_receivables_outstanding: Decimal


# ==============================================================================
# Money / quantity helpers (no float arithmetic on money)
# ==============================================================================

def to_money(value: Any) -> Decimal:
    """Convert a float/str/int/Decimal to a 2-dp Decimal (ROUND_HALF_UP). Floats go via str()."""
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"Non-finite monetary value: {value!r}")
    return d.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def to_qty(value: Any) -> Decimal:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        raise ValueError(f"Non-finite quantity: {value!r}")
    return d.quantize(_QTY_Q, rounding=ROUND_HALF_UP)


def to_minor(amount: Decimal) -> int:
    """Exact integer paise."""
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_minor(minor: int) -> Decimal:
    return (Decimal(int(minor)) / 100).quantize(_MONEY_Q)


def _f(d: Decimal) -> float:
    """Decimal -> float for storage in legacy float-typed fields (value is already quantized)."""
    return float(d)


def _norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def _unit_key(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    u = _norm(unit)
    return _UNIT_SYNONYMS.get(u, u)


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _snapshot_hash(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def candidate_id_for(merchant_id: str, source_message_id: str, candidate_index: int = 0) -> str:
    """Stable, merchant-scoped candidate id derived from the source event."""
    return "cand_" + _sha(merchant_id, source_message_id, str(candidate_index))[:32]


def _confirmation_doc_id(merchant_id: str, request_id: str) -> str:
    return "conf_" + _sha(merchant_id, request_id)[:32]


def _txn_id_for(candidate_id: str) -> str:
    return "txn_" + candidate_id[len("cand_"):]


# ==============================================================================
# Firestore plumbing
# ==============================================================================

_client: Optional[firestore.Client] = None


def get_db() -> firestore.Client:
    """Process-wide Firestore client. Honors FIRESTORE_EMULATOR_HOST automatically."""
    global _client
    if _client is None:
        _client = firestore.Client(project=settings.GCP_PROJECT_ID, database=settings.FIRESTORE_DATABASE)
    return _client


def _run_txn(db, fn, outer_attempts: int = 4):
    """Run a @firestore.transactional callable. Firestore retries Aborted internally; if heavy
    contention exhausts those retries we retry the whole (idempotent, re-reading) operation with
    jittered backoff instead of surfacing a spurious failure to the caller."""
    for attempt in range(outer_attempts):
        try:
            return fn(db.transaction(max_attempts=10))
        except (ValueError, Aborted) as exc:
            exhausted = isinstance(exc, Aborted) or "Failed to commit transaction" in str(exc)
            if not exhausted or attempt == outer_attempts - 1:
                raise
            time_module.sleep(random.uniform(0.05, 0.25) * (attempt + 1))


def _require_str(name: str, value: Optional[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _mref(db, merchant_id: str):
    return db.collection(COL_MERCHANTS).document(merchant_id)


def _col(db, merchant_id: str, name: str):
    return _mref(db, merchant_id).collection(name)


def _is_authorized(merchant_doc: Dict[str, Any], merchant_id: str, actor: str) -> bool:
    actor_d = _digits(actor)
    if not actor_d:
        return False
    allowed = {_digits(merchant_id)} | {_digits(p) for p in merchant_doc.get("authorized_phones", []) or []}
    return actor_d in allowed


def _require_merchant(db, merchant_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    snap = _mref(db, merchant_id).get()
    if not snap.exists:
        raise MerchantNotFoundError(f"Merchant {merchant_id} not found")
    data = snap.to_dict() or {}
    if actor is not None and not _is_authorized(data, merchant_id, actor):
        raise NotAuthorizedError("Actor is not authorized for this merchant")
    return data


_SEVERITY = {"ACCEPTED": 0, "NEEDS_REVIEW": 1, "REJECTED": 2}


# ==============================================================================
# Pure planning: snapshot -> deterministic posting plan (no I/O)
# ==============================================================================

class _Hold(Exception):
    """Retryable hold: needs human/catalog attention."""

    def __init__(self, *reasons: str):
        self.reasons = list(reasons)
        super().__init__("; ".join(reasons))


class _Reject(Exception):
    """Terminal: snapshot is arithmetically/semantically invalid."""

    def __init__(self, *reasons: str):
        self.reasons = list(reasons)
        super().__init__("; ".join(reasons))


@dataclass
class _Line:
    item_name: str
    quantity: Decimal
    unit: Optional[str]
    unit_price: Decimal
    line_total: Decimal


@dataclass
class _Plan:
    kind: str                                  # sale | payment_received | stock_added
    intent: TransactionIntent
    total: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    lines: List[_Line] = field(default_factory=list)
    receipts: List[Tuple[str, Decimal]] = field(default_factory=list)   # (account, amount)
    credit: Decimal = Decimal("0.00")
    party_name: Optional[str] = None
    business_date: str = ""

    @property
    def received(self) -> Decimal:
        return sum((a for _, a in self.receipts), Decimal("0.00"))


def _plan_from_snapshot(snap: TransactionCandidate, today_ist: date) -> _Plan:
    """Derive the posting plan from the immutable snapshot using exact arithmetic."""
    t = snap.transaction_type
    if t == TransactionType.SALE:
        kind, intent = "sale", TransactionIntent.SALE_INVOICE
    elif t == TransactionType.PAYMENT_RECEIVED:
        kind, intent = "payment_received", TransactionIntent.UDHAAR_COLLECTION
    elif t == TransactionType.STOCK_ADDED:
        kind, intent = "stock_added", TransactionIntent.PRODUCTION_LOG
    else:
        raise _Hold(f"Transaction type '{t.value}' is not supported by the ledger MVP; manual review required")

    plan = _Plan(kind=kind, intent=intent, party_name=(snap.buyer_name or "").strip() or None)

    # Business date
    if snap.transaction_date:
        try:
            bd = date.fromisoformat(snap.transaction_date)
        except ValueError:
            raise _Hold(f"Unparseable transaction_date '{snap.transaction_date}'")
        if bd > today_ist:
            raise _Hold(f"transaction_date {bd} is in the future")
        plan.business_date = bd.isoformat()
    else:
        plan.business_date = today_ist.isoformat()

    # Line items
    try:
        for it in snap.items:
            if it.quantity is None:
                raise _Hold(f"Item '{it.item_name}' has no quantity")
            qty = to_qty(it.quantity)
            if qty <= 0:
                raise _Reject(f"Item '{it.item_name}' has non-positive quantity")
            if it.line_total is not None:
                lt = to_money(it.line_total)
                up = to_money(it.unit_price) if it.unit_price is not None else (lt / qty).quantize(_MONEY_Q, ROUND_HALF_UP)
            elif it.unit_price is not None:
                up = to_money(it.unit_price)
                lt = (qty * up).quantize(_MONEY_Q, ROUND_HALF_UP)
            elif kind == "stock_added":
                up, lt = Decimal("0.00"), Decimal("0.00")
            else:
                raise _Hold(f"Item '{it.item_name}' has neither unit price nor line total")
            if lt < 0 or up < 0:
                raise _Reject(f"Item '{it.item_name}' has a negative amount")
            plan.lines.append(_Line(it.item_name, qty, it.unit, up, lt))
    except ValueError as exc:
        raise _Reject(str(exc))

    if kind == "stock_added":
        if not plan.lines:
            raise _Hold("stock_added without any items")
        if snap.payments or snap.credit_amount or snap.total_amount:
            raise _Hold("stock_added carries money fields; production entries must not record revenue")
        return plan

    try:
        if snap.total_amount is None:
            raise _Hold("total_amount is missing")
        plan.total = to_money(snap.total_amount)
        plan.tax = to_money(snap.tax_amount or 0)
        plan.discount = to_money(snap.discount_amount or 0)
        receipts = _receipts_from_snapshot(snap)
        credit_stated = to_money(snap.credit_amount) if snap.credit_amount is not None else None
    except ValueError as exc:
        raise _Reject(str(exc))

    if plan.total <= 0:
        raise _Reject("Total amount must be positive")
    if plan.tax < 0 or plan.discount < 0 or any(a <= 0 for _, a in receipts):
        raise _Reject("Negative or zero amounts are not allowed")
    plan.receipts = receipts
    paid = plan.received

    if kind == "payment_received":
        if plan.lines:
            raise _Hold("payment_received also lists items; ambiguous between sale and collection")
        if not receipts:
            raise _Hold("Payment method/amount missing for received payment")
        if credit_stated:
            raise _Reject("A collection cannot also create new credit")
        plan.total = paid
        plan.credit = Decimal("0.00")
        if not plan.party_name:
            raise _Hold("Customer name missing for payment collection")
        return plan

    # --- sale ---
    net_before_tax = plan.total - plan.tax + plan.discount
    if plan.lines:
        items_sum = sum((l.line_total for l in plan.lines), Decimal("0.00"))
        expected = to_money(snap.subtotal) if snap.subtotal is not None else net_before_tax
        if items_sum != expected:
            raise _Reject(f"Line items sum {items_sum} != expected {expected}")
        if snap.subtotal is not None and to_money(snap.subtotal) != net_before_tax:
            raise _Reject(f"subtotal {to_money(snap.subtotal)} + tax - discount != total {plan.total}")
    if paid > plan.total:
        raise _Reject(f"Received {paid} exceeds sale total {plan.total}")
    remainder = plan.total - paid
    if credit_stated is not None and credit_stated != remainder:
        raise _Reject(f"Stated credit {credit_stated} != total {plan.total} - paid {paid}")
    if remainder > 0 and credit_stated is None:
        explicit = snap.payment_status in (ExtractedPaymentStatus.UNPAID, ExtractedPaymentStatus.PARTIAL) or (
            snap.amount_paid is not None
        )
        if not explicit:
            raise _Hold("Payment status unspecified: unpaid remainder was never stated as credit")
    plan.credit = remainder
    if plan.credit > 0 and not plan.party_name:
        raise _Hold("Customer name required to record credit (udhaar)")
    return plan


def _receipts_from_snapshot(snap: TransactionCandidate) -> List[Tuple[str, Decimal]]:
    out: List[Tuple[str, Decimal]] = []
    if snap.payments:
        for p in snap.payments:
            acc = _METHOD_ACCOUNT.get(_norm(p.method).replace(" ", "_"))
            if acc is None:
                raise _Hold(f"Unknown payment method '{p.method}'")
            out.append((acc, to_money(p.amount)))
    else:
        if snap.cash_amount:
            out.append((ACC_CASH, to_money(snap.cash_amount)))
        if snap.online_amount:
            out.append((ACC_UPI, to_money(snap.online_amount)))
        if not out and snap.amount_paid:
            raise _Hold("Amount paid is stated without a payment method (cash/UPI/bank)")
    return out


# ------------------------------------------------------------------------------
# Catalog resolution (exact normalized match; ambiguity or absence => hold)
# ------------------------------------------------------------------------------

def _resolve_one(name: str, docs: Dict[str, Dict[str, Any]], name_fields: Tuple[str, ...], what: str) -> str:
    target = _norm(name)
    hits = []
    for doc_id, d in docs.items():
        names = {_norm(doc_id), _norm(doc_id.replace("-", " "))}
        for f in name_fields:
            v = d.get(f)
            if isinstance(v, str):
                names.add(_norm(v))
            elif isinstance(v, list):
                names.update(_norm(x) for x in v if isinstance(x, str))
        if target and target in names:
            hits.append(doc_id)
    if not hits:
        raise _Hold(f"Unknown {what} '{name}'; not auto-creating")
    if len(hits) > 1:
        raise _Hold(f"Ambiguous {what} '{name}' matches {sorted(hits)}")
    return hits[0]


def _name_matches(doc: Dict[str, Any], doc_id: str, name: str, fields: Tuple[str, ...]) -> bool:
    try:
        return _resolve_one(name, {doc_id: doc}, fields, "x") == doc_id
    except _Hold:
        return False


def _load_catalog(db, merchant_id: str, plan: _Plan) -> Tuple[Dict[str, str], Optional[str]]:
    """Resolve item names -> sku_id and customer -> party_id (read-only, outside the txn)."""
    sku_for_line: Dict[str, str] = {}
    if plan.lines:
        inv = {s.id: (s.to_dict() or {}) for s in _col(db, merchant_id, COL_INVENTORY).stream()}
        for line in plan.lines:
            sku_for_line[line.item_name] = _resolve_one(
                line.item_name, inv, ("item_name", "aliases", "sku_id"), "product/SKU"
            )
            sku_unit = _unit_key(inv[sku_for_line[line.item_name]].get("unit"))
            line_unit = _unit_key(line.unit)
            if line_unit and sku_unit and line_unit != sku_unit:
                raise _Hold(
                    f"Unit mismatch for '{line.item_name}': stated '{line.unit}', catalog '{inv[sku_for_line[line.item_name]].get('unit')}'"
                )
    party_id = None
    if plan.party_name:
        parties = {s.id: (s.to_dict() or {}) for s in _col(db, merchant_id, COL_PARTIES).stream()}
        party_id = _resolve_one(plan.party_name, parties, ("display_name", "aliases", "party_id"), "customer")
    return sku_for_line, party_id


def _assess(supplied: TransactionCandidate, merchant: Dict[str, Any]):
    """Server-side validation of a candidate -> (status, warnings, posting_status, hold_reasons).

    Re-runs BusinessValidator on a copy (the supplied validation_status is never trusted; the
    stricter verdict wins) and then the ledger's own deterministic plan. NEEDS_REVIEW
    candidates are held until an authorized merchant resolves them via review_candidate."""
    recomputed = BusinessValidator.validate_candidate(supplied.model_copy(deep=True), merchant.get("business_name"))
    status = max(supplied.validation_status, recomputed.validation_status, key=_SEVERITY.__getitem__)
    warnings = list(dict.fromkeys(list(supplied.validation_warnings) + list(recomputed.validation_warnings)))
    if status == "REJECTED":
        return status, warnings, PostingStatus.REJECTED, warnings or ["Rejected by validation"]
    if status == "NEEDS_REVIEW":
        reasons = list(warnings)
        if supplied.needs_clarification or recomputed.needs_clarification:
            reasons.insert(0, "Clarification required: " + (
                supplied.clarification_question or recomputed.clarification_question or "details missing"))
        return status, warnings, PostingStatus.NEEDS_REVIEW, reasons or ["Review required"]
    try:
        _plan_from_snapshot(supplied, datetime.now(IST).date())
    except _Hold as h:
        return status, warnings, PostingStatus.NEEDS_REVIEW, h.reasons
    except _Reject as r:
        return "REJECTED", warnings, PostingStatus.REJECTED, r.reasons
    return status, warnings, PostingStatus.PENDING_CONFIRMATION, []


# ==============================================================================
# stage_candidate
# ==============================================================================

def stage_candidate(
    merchant_id: str,
    source_message_id: str,
    candidate: Any,
    *,
    candidate_index: int = 0,
    source_channel: str = "meta_whatsapp",
    source_media_type: str = "text",
    ttl: timedelta = CANDIDATE_TTL,
    db: Optional[firestore.Client] = None,
    now: Optional[datetime] = None,
) -> StagedCandidate:
    """
    Persist an extracted candidate as an immutable snapshot awaiting confirmation.

    * Idempotent on (merchant_id, source_message_id, candidate_index): a repeat returns
      the existing candidate (``already_staged=True``) and never overwrites the snapshot.
    * validation_status is recomputed server-side with BusinessValidator and the stricter
      of (supplied, recomputed) wins; a client-supplied ACCEPTED is never trusted.
    * Only ACCEPTED candidates become PENDING_CONFIRMATION. NEEDS_REVIEW candidates are held
      and can only be released through review_candidate().
    """
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    source_message_id = _require_str("source_message_id", source_message_id)
    merchant = _require_merchant(db, merchant_id)

    if isinstance(candidate, TransactionCandidate):
        supplied = candidate.model_copy(deep=True)
    else:
        supplied = TransactionCandidate.model_validate(candidate)

    status, warnings, posting, hold_reasons = _assess(supplied, merchant)

    now = now or datetime.now(timezone.utc)
    cid = candidate_id_for(merchant_id, source_message_id, candidate_index)
    ref = _col(db, merchant_id, COL_CANDIDATES).document(cid)
    snapshot = supplied.model_dump(mode="json")
    doc = {
        "candidate_id": cid,
        "merchant_id": merchant_id,
        "source_message_id": source_message_id,
        "candidate_index": candidate_index,
        "source_channel": source_channel,
        "source_media_type": source_media_type,
        "transaction_type": supplied.transaction_type.value,
        "validation_status": status,
        "validation_warnings": warnings,
        "posting_status": posting.value,
        "hold_reasons": hold_reasons,
        "hold_source": "EXTRACTION" if posting != PostingStatus.PENDING_CONFIRMATION else None,
        "originally_needs_review": status == "NEEDS_REVIEW",
        "review_resolved": False,
        "review_count": 0,
        "review_history": [],
        "original_extraction": snapshot,          # never modified after staging
        "original_snapshot_sha256": _snapshot_hash(snapshot),
        "created_at": now,
        "expires_at": now + ttl,
        "confirmed_at": None,
        "confirmed_by": None,
        "confirmation_request_id": None,
        "committed_txn_id": None,
        "extracted_snapshot": snapshot,
        "snapshot_sha256": _snapshot_hash(snapshot),
        "status_history": [{"at": now, "from": None, "to": posting.value, "by": "system", "note": "staged"}],
        "ledger_version": LEDGER_VERSION,
    }
    try:
        ref.create(doc)  # fails with AlreadyExists if this source event was staged before
        created = True
    except AlreadyExists:
        created = False
    if not created:
        existing = ref.get().to_dict() or {}
        return StagedCandidate(
            candidate_id=cid, merchant_id=merchant_id, source_message_id=source_message_id,
            validation_status=existing.get("validation_status", status),
            posting_status=PostingStatus(existing.get("posting_status", posting.value)),
            hold_reasons=existing.get("hold_reasons", []),
            validation_warnings=existing.get("validation_warnings", []),
            already_staged=True, expires_at=existing.get("expires_at"),
        )
    return StagedCandidate(
        candidate_id=cid, merchant_id=merchant_id, source_message_id=source_message_id,
        validation_status=status, posting_status=posting, hold_reasons=hold_reasons,
        validation_warnings=warnings, already_staged=False, expires_at=doc["expires_at"],
    )


# ==============================================================================
# confirm_and_commit
# ==============================================================================

def _acct_data(snap, code: str) -> Dict[str, int]:
    d = snap.to_dict() if snap.exists else {}
    return {"debit": int((d or {}).get("debit_total_minor", 0)), "credit": int((d or {}).get("credit_total_minor", 0))}


def _journal_lines(plan: _Plan) -> List[Dict[str, Any]]:
    """Balanced double-entry lines in minor units. Empty for stock-only entries."""
    lines: List[Dict[str, Any]] = []
    if plan.kind == "sale":
        for acc, amt in plan.receipts:
            lines.append({"account": acc, "debit_minor": to_minor(amt), "credit_minor": 0})
        if plan.credit > 0:
            lines.append({"account": ACC_AR, "debit_minor": to_minor(plan.credit), "credit_minor": 0})
        lines.append({"account": ACC_SALES, "debit_minor": 0, "credit_minor": to_minor(plan.total - plan.tax)})
        if plan.tax > 0:
            lines.append({"account": ACC_GST, "debit_minor": 0, "credit_minor": to_minor(plan.tax)})
    elif plan.kind == "payment_received":
        for acc, amt in plan.receipts:
            lines.append({"account": acc, "debit_minor": to_minor(amt), "credit_minor": 0})
        lines.append({"account": ACC_AR, "debit_minor": 0, "credit_minor": to_minor(plan.received)})
    dr = sum(l["debit_minor"] for l in lines)
    cr = sum(l["credit_minor"] for l in lines)
    if dr != cr:
        raise LedgerImbalanceError(f"Journal does not balance: debits {dr} != credits {cr}")
    return lines


def _party_balance(d: Dict[str, Any]) -> Decimal:
    """Legacy-compatible: prefer exact minor units, fall back to the seeded float field."""
    if d.get("udhaar_balance_minor") is not None:
        return from_minor(d["udhaar_balance_minor"])
    return to_money(d.get("current_udhaar_balance", 0.0))


def _stock(d: Dict[str, Any]) -> Decimal:
    return to_qty(d.get("current_stock", 0.0))


def _apply_writes(transaction, writes: List[Tuple[str, Any, Dict[str, Any]]]) -> None:
    """Queue all writes on the Firestore transaction. Separate function so tests can inject faults."""
    for op, ref, data in writes:
        if op == "create":
            transaction.create(ref, data)
        elif op == "update":
            transaction.update(ref, data)
        elif op == "set":
            transaction.set(ref, data)
        else:  # pragma: no cover
            raise AssertionError(op)


def confirm_and_commit(
    merchant_id: str,
    candidate_id: str,
    *,
    confirmed_by: str,
    confirmation_request_id: Optional[str] = None,
    expected_snapshot_sha256: Optional[str] = None,
    db: Optional[firestore.Client] = None,
    now: Optional[datetime] = None,
) -> CommitResult:
    """
    Atomically post a confirmed candidate. All-or-nothing in one Firestore transaction.

    Raises: MerchantNotFoundError, NotAuthorizedError, CandidateNotFoundError,
    InvalidStateError, StaleConfirmationError, IdempotencyConflictError,
    HeldForReviewError (candidate -> NEEDS_REVIEW, nothing posted),
    ValidationFailedError (candidate -> REJECTED, nothing posted).

    Idempotent: replays of the same confirmation_request_id, or any confirmation of an
    already POSTED candidate, return the original result with ``idempotent_replay=True``.
    """
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    candidate_id = _require_str("candidate_id", candidate_id)
    confirmed_by = _require_str("confirmed_by", confirmed_by)
    request_id = confirmation_request_id.strip() if confirmation_request_id else f"confirm:{candidate_id}"
    now = now or datetime.now(timezone.utc)
    today_ist = now.astimezone(IST).date()

    # ---- Pre-transaction (read-only) -------------------------------------------------
    _require_merchant(db, merchant_id, actor=confirmed_by)
    cand_ref = _col(db, merchant_id, COL_CANDIDATES).document(candidate_id)
    pre = cand_ref.get()
    if not pre.exists:
        raise CandidateNotFoundError(f"Candidate {candidate_id} not found for merchant")
    pre_d = pre.to_dict() or {}
    if pre_d.get("merchant_id") != merchant_id:
        raise CandidateNotFoundError(f"Candidate {candidate_id} not found for merchant")

    # Resolution/plan are only needed if the candidate is still postable; the transaction
    # re-checks state authoritatively, so a stale pre-read can never cause a double post.
    plan: Optional[_Plan] = None
    sku_for_line: Dict[str, str] = {}
    party_id: Optional[str] = None
    pre_hold: Optional[_Hold] = None
    postable = pre_d.get("posting_status") == PostingStatus.PENDING_CONFIRMATION.value or (
        pre_d.get("posting_status") == PostingStatus.NEEDS_REVIEW.value and pre_d.get("hold_source") == "LEDGER"
    )
    # Integrity first: a tampered snapshot is never planned; the transaction reports it.
    if postable and _snapshot_hash(pre_d.get("extracted_snapshot", {})) == pre_d.get("snapshot_sha256"):
        try:
            snap_model = TransactionCandidate.model_validate(pre_d["extracted_snapshot"])
            plan = _plan_from_snapshot(snap_model, today_ist)
            sku_for_line, party_id = _load_catalog(db, merchant_id, plan)
        except _Hold as h:
            pre_hold = h
        except _Reject as r:
            _mark(db, cand_ref, PostingStatus.REJECTED, r.reasons, now, "LEDGER", confirmed_by)
            raise ValidationFailedError(r.reasons)

    refs = _Refs(db, merchant_id, candidate_id, request_id, plan, sku_for_line, party_id)
    holder: Dict[str, Any] = {}

    @firestore.transactional
    def _txn(transaction):
        return _commit_callback(
            transaction, refs, plan, sku_for_line, party_id, pre_hold,
            confirmed_by, request_id, expected_snapshot_sha256, now, today_ist,
        )

    outcome = _run_txn(db, _txn)
    kind = outcome["kind"]
    if kind == "POSTED":
        return CommitResult(**outcome["result"])
    if kind == "REPLAY":
        r = dict(outcome["result"])
        r["idempotent_replay"] = True
        return CommitResult(**r)
    if kind == "HELD":
        raise HeldForReviewError(outcome["reasons"])
    if kind == "REJECTED":
        raise ValidationFailedError(outcome["reasons"])
    if kind == "STALE":
        raise StaleConfirmationError(outcome["reason"])
    if kind == "CONFLICT":
        raise IdempotencyConflictError(outcome["reason"])
    if kind == "INVALID_STATE":
        raise InvalidStateError(outcome["reason"])
    raise AssertionError(f"unexpected outcome {kind}")  # pragma: no cover


class _Refs:
    """Document references used by the commit transaction (built once, outside the callback)."""

    def __init__(self, db, merchant_id, candidate_id, request_id, plan, sku_for_line, party_id):
        self.merchant = _mref(db, merchant_id)
        self.candidate = _col(db, merchant_id, COL_CANDIDATES).document(candidate_id)
        self.confirmation = _col(db, merchant_id, COL_CONFIRMATIONS).document(
            _confirmation_doc_id(merchant_id, request_id)
        )
        self.txn_id = _txn_id_for(candidate_id)
        self.txn = _col(db, merchant_id, COL_TRANSACTIONS).document(self.txn_id)
        self.party = _col(db, merchant_id, COL_PARTIES).document(party_id) if party_id else None
        self.skus = {
            sku: _col(db, merchant_id, COL_INVENTORY).document(sku) for sku in set(sku_for_line.values())
        }
        self.accounts = {
            code: _col(db, merchant_id, COL_ACCOUNTS).document(code)
            for code in (ACC_CASH, ACC_UPI, ACC_BANK, ACC_AR, ACC_SALES, ACC_GST)
        }
        self.journal = _col(db, merchant_id, COL_JOURNAL).document("jrnl_" + self.txn_id[4:])
        self.payments_col = _col(db, merchant_id, COL_PAYMENTS)
        self.movements_col = _col(db, merchant_id, COL_MOVEMENTS)
        self.merchant_id = merchant_id
        self.candidate_id = candidate_id


def _mark(db, cand_ref, status: PostingStatus, reasons: List[str], now, source: str, by: str) -> None:
    """Non-financial candidate state change (hold/reject) outside the commit transaction."""
    cand_ref.update({
        "posting_status": status.value,
        "hold_reasons": reasons,
        "hold_source": source,
        "status_history": firestore.ArrayUnion([{"at": now, "from": None, "to": status.value, "by": by, "note": "; ".join(reasons)[:500]}]),
    })


def _commit_callback(
    transaction, refs: _Refs, plan: Optional[_Plan], sku_for_line, party_id, pre_hold,
    confirmed_by, request_id, expected_sha, now, today_ist,
) -> Dict[str, Any]:
    """
    Pure, retry-safe transaction body: reads first, then queued writes. All inputs are
    immutable per attempt; ids are deterministic; ``now`` is fixed by the caller.
    """
    # ---------------- READS (all before any write) ----------------
    read_refs = [refs.merchant, refs.candidate, refs.confirmation, refs.txn]
    if refs.party:
        read_refs.append(refs.party)
    read_refs += list(refs.skus.values())
    read_refs += list(refs.accounts.values())
    snaps = {s.reference.path: s for s in transaction.get_all(read_refs)}
    get = lambda r: snaps[r.path]  # noqa: E731

    merchant_d = get(refs.merchant).to_dict() if get(refs.merchant).exists else None
    if merchant_d is None or not _is_authorized(merchant_d, refs.merchant_id, confirmed_by):
        return {"kind": "INVALID_STATE", "reason": "Merchant missing or actor not authorized"}

    cand_snap = get(refs.candidate)
    if not cand_snap.exists:
        return {"kind": "INVALID_STATE", "reason": "Candidate disappeared"}
    cand = cand_snap.to_dict()
    if cand.get("merchant_id") != refs.merchant_id:
        return {"kind": "INVALID_STATE", "reason": "Candidate merchant mismatch"}

    # Idempotency: same confirmation request id
    conf_snap = get(refs.confirmation)
    if conf_snap.exists:
        conf = conf_snap.to_dict()
        if conf.get("candidate_id") != refs.candidate_id:
            return {"kind": "CONFLICT", "reason": "confirmation_request_id already used for a different candidate"}
        return {"kind": "REPLAY", "result": conf["result"]}

    posting = cand.get("posting_status")
    if posting == PostingStatus.POSTED.value:
        txn_snap = get(refs.txn)
        if not txn_snap.exists:
            return {"kind": "INVALID_STATE", "reason": "Candidate is POSTED but transaction is missing (data corruption)"}
        return {"kind": "REPLAY", "result": cand["commit_result"]}

    if cand.get("originally_needs_review") and not cand.get("review_resolved"):
        return {"kind": "INVALID_STATE", "reason": "Candidate needs explicit merchant review (review_candidate) before confirmation"}
    retryable_hold = posting == PostingStatus.NEEDS_REVIEW.value and cand.get("hold_source") == "LEDGER"
    if posting != PostingStatus.PENDING_CONFIRMATION.value and not retryable_hold:
        return {"kind": "INVALID_STATE", "reason": f"Candidate is {posting}; only PENDING_CONFIRMATION candidates can be confirmed"}

    if cand.get("expires_at") and cand["expires_at"] < now:
        return {"kind": "STALE", "reason": "Confirmation arrived after the candidate expired"}
    snapshot = cand["extracted_snapshot"]
    actual_sha = _snapshot_hash(snapshot)
    if actual_sha != cand.get("snapshot_sha256"):
        return {"kind": "INVALID_STATE", "reason": "Snapshot integrity check failed (snapshot was modified)"}
    if cand.get("original_extraction") is not None and _snapshot_hash(cand["original_extraction"]) != cand.get("original_snapshot_sha256"):
        return {"kind": "INVALID_STATE", "reason": "Original extraction integrity check failed"}
    if expected_sha and expected_sha != actual_sha:
        return {"kind": "STALE", "reason": "Confirmation refers to a different snapshot than the one staged"}

    def hold_outcome(kind: str, reasons: List[str], status: PostingStatus):
        transaction.update(refs.candidate, {
            "posting_status": status.value,
            "hold_reasons": reasons,
            "hold_source": "LEDGER",
            "status_history": firestore.ArrayUnion([{"at": now, "from": posting, "to": status.value, "by": confirmed_by, "note": "; ".join(reasons)[:500]}]),
        })
        return {"kind": kind, "reasons": reasons}

    if pre_hold is not None or plan is None:
        return hold_outcome("HELD", (pre_hold.reasons if pre_hold else ["Candidate could not be planned"]), PostingStatus.NEEDS_REVIEW)

    # Server-side revalidation: the stored status is not trusted.
    snap_model = TransactionCandidate.model_validate(snapshot)
    try:
        plan = _plan_from_snapshot(snap_model, today_ist)
    except _Hold as h:
        return hold_outcome("HELD", h.reasons, PostingStatus.NEEDS_REVIEW)
    except _Reject as r:
        return hold_outcome("REJECTED", r.reasons, PostingStatus.REJECTED)
    if BusinessValidator.validate_candidate(
        snap_model.model_copy(deep=True), merchant_d.get("business_name")
    ).validation_status == "REJECTED":
        return hold_outcome("REJECTED", ["Rejected by Stage-4 revalidation"], PostingStatus.REJECTED)

    # Customer still matches the one resolved before the transaction began?
    party_d = None
    if refs.party is not None:
        if not get(refs.party).exists:
            return hold_outcome("HELD", [f"Customer '{plan.party_name}' no longer exists"], PostingStatus.NEEDS_REVIEW)
        party_d = get(refs.party).to_dict()
        if not _name_matches(party_d, refs.party.id, plan.party_name or "", ("display_name", "aliases", "party_id")):
            return hold_outcome("HELD", [f"Customer '{plan.party_name}' changed during confirmation"], PostingStatus.NEEDS_REVIEW)

    # ---------------- Compute (pure) ----------------
    writes: List[Tuple[str, Any, Dict[str, Any]]] = []
    txn_id = refs.txn_id
    movement_ids: List[str] = []
    stock_levels: Dict[str, Decimal] = {}
    for sku, ref in refs.skus.items():
        s = get(ref)
        if not s.exists:
            return hold_outcome("HELD", [f"SKU '{sku}' not found"], PostingStatus.NEEDS_REVIEW)
        stock_levels[sku] = _stock(s.to_dict())
    movement_rows: List[Dict[str, Any]] = []
    txn_lines: List[LineItem] = []
    for i, line in enumerate(plan.lines):
        sku = sku_for_line.get(line.item_name)
        if sku is None:
            return hold_outcome("HELD", [f"No SKU resolved for '{line.item_name}'"], PostingStatus.NEEDS_REVIEW)
        if plan.kind == "sale":
            delta, mtype = -line.quantity, MovementType.SALE_DEDUCT
        else:
            delta, mtype = line.quantity, MovementType.PRODUCTION_ADD
        new_level = stock_levels[sku] + delta
        if new_level < 0:
            return hold_outcome(
                "HELD",
                [f"Insufficient stock for '{line.item_name}' ({sku}): have {stock_levels[sku]}, need {line.quantity}"],
                PostingStatus.NEEDS_REVIEW,
            )
        stock_levels[sku] = new_level
        mid = f"mv_{txn_id[4:]}_{i}"
        movement_ids.append(mid)
        movement_rows.append({
            "movement_id": mid, "merchant_id": refs.merchant_id, "sku_id": sku,
            "movement_type": mtype.value, "quantity": _f(delta), "balance_after": _f(new_level),
            "reference_txn_id": txn_id, "line_index": i, "created_at": now,
        })
        txn_lines.append(LineItem(
            sku_id=sku, item_name=line.item_name, quantity=_f(line.quantity), unit=line.unit or "pieces",
            unit_price=_f(line.unit_price), discount=0.0, tax_rate=0.0, subtotal=_f(line.line_total),
        ))

    new_party_balance: Optional[Decimal] = None
    if party_d is not None:
        bal = _party_balance(party_d)
        if plan.kind == "sale":
            new_party_balance = bal + plan.credit
            limit = to_money(party_d.get("credit_limit", 0.0))
            if plan.credit > 0 and new_party_balance > limit:
                return hold_outcome("HELD", [f"Credit limit exceeded: {new_party_balance} > {limit}"], PostingStatus.NEEDS_REVIEW)
        elif plan.kind == "payment_received":
            if plan.received > bal:
                return hold_outcome("HELD", [f"Payment {plan.received} exceeds outstanding receivable {bal}"], PostingStatus.NEEDS_REVIEW)
            if to_minor(plan.received) > int(party_d.get("ar_posted_minor", 0)):
                return hold_outcome("HELD", [
                    f"Receivable for '{refs.party.id}' has no opening-balance posting; run post_opening_balance first"
                ], PostingStatus.NEEDS_REVIEW)
            new_party_balance = bal - plan.received

    gate = False
    if plan.kind == "sale":
        thr = to_money(merchant_d.get("high_credit_threshold", settings.HIGH_CREDIT_THRESHOLD))
        gate = plan.credit > thr or snap_model.confidence < settings.CONFIDENCE_THRESHOLD

    journal = _journal_lines(plan)  # raises LedgerImbalanceError before any write

    # Transaction document (existing Transaction model + ledger extras)
    nodes: List[PaymentNode] = [
        PaymentNode(method=_ACCOUNT_METHOD[acc], amount=_f(amt),
                    status=PaymentStatus.APPLIED if plan.kind == "payment_received" else PaymentStatus.SETTLED)
        for acc, amt in plan.receipts
    ]
    if plan.credit > 0:
        nodes.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=_f(plan.credit),
                                 status=PaymentStatus.PENDING_COLLECTION, due_date=snap_model.due_date or snap_model.due_date_text))
    txn_model = Transaction(
        txn_id=txn_id, merchant_id=refs.merchant_id, intent=plan.intent,
        source_channel=cand.get("source_channel", "meta_whatsapp"),
        source_media_type=cand.get("source_media_type", "text"),
        party_id=refs.party.id if refs.party else None, party_name=plan.party_name,
        line_items=txn_lines, payments=nodes, total_amount=_f(plan.total),
        status=TransactionStatus.COMMITTED, confidence_score=max(0.0, min(1.0, snap_model.confidence)),
        safety_gate_triggered=gate, idempotency_key=f"{refs.merchant_id}:{cand['source_message_id']}:{cand.get('candidate_index', 0)}",
        raw_transcript=snap_model.raw_transcript,
        created_at=cand["created_at"], committed_at=now,
    )
    txn_doc = txn_model.model_dump(mode="json")
    txn_doc["created_at"], txn_doc["committed_at"] = cand["created_at"], now
    txn_doc.update({
        "candidate_id": refs.candidate_id, "source_message_id": cand["source_message_id"],
        "confirmation_request_id": request_id, "confirmed_by": confirmed_by,
        "business_date": plan.business_date,
        "total_minor": to_minor(plan.total), "received_minor": to_minor(plan.received),
        "credit_minor": to_minor(plan.credit), "tax_minor": to_minor(plan.tax),
        "journal_id": refs.journal.id if journal else None, "movement_ids": movement_ids,
        "ledger_version": LEDGER_VERSION,
    })
    writes.append(("create", refs.txn, txn_doc))  # create() => fails if the txn already exists

    for row in movement_rows:
        writes.append(("create", refs.movements_col.document(row["movement_id"]), row))
    for sku, level in stock_levels.items():
        writes.append(("update", refs.skus[sku], {"current_stock": _f(level), "updated_at": now}))

    for i, (acc, amt) in enumerate(plan.receipts):
        writes.append(("create", refs.payments_col.document(f"pay_{txn_id[4:]}_{i}"), {
            "payment_id": f"pay_{txn_id[4:]}_{i}", "merchant_id": refs.merchant_id, "txn_id": txn_id,
            "party_id": refs.party.id if refs.party else None, "direction": "IN",
            "kind": "DEBT_COLLECTION" if plan.kind == "payment_received" else "SALE_PAYMENT",
            "method": _ACCOUNT_METHOD[acc].value, "account": acc, "amount_minor": to_minor(amt),
            "amount": _f(amt), "received_at": now, "business_date": plan.business_date,
        }))

    if refs.party is not None and new_party_balance is not None:
        writes.append(("update", refs.party, {
            "current_udhaar_balance": _f(new_party_balance),
            "udhaar_balance_minor": to_minor(new_party_balance),
            "ar_posted_minor": int(party_d.get("ar_posted_minor", 0)) + (
                to_minor(plan.credit) if plan.kind == "sale" else -to_minor(plan.received)),
            "last_txn_at": now,
        }))

    if journal:
        writes.append(("create", refs.journal, {
            "journal_id": refs.journal.id, "merchant_id": refs.merchant_id, "txn_id": txn_id,
            "lines": journal, "total_debit_minor": sum(l["debit_minor"] for l in journal),
            "total_credit_minor": sum(l["credit_minor"] for l in journal),
            "business_date": plan.business_date, "created_at": now,
        }))
        by_acc: Dict[str, List[int]] = {}
        for l in journal:
            d = by_acc.setdefault(l["account"], [0, 0])
            d[0] += l["debit_minor"]
            d[1] += l["credit_minor"]
        for code, (dr, cr) in by_acc.items():
            cur = _acct_data(get(refs.accounts[code]), code)
            dr_t, cr_t = cur["debit"] + dr, cur["credit"] + cr
            writes.append(("set", refs.accounts[code], {
                "code": code, "debit_total_minor": dr_t, "credit_total_minor": cr_t,
                "balance_minor": dr_t - cr_t, "updated_at": now,
            }))

    result = {
        "candidate_id": refs.candidate_id, "txn_id": txn_id, "merchant_id": refs.merchant_id,
        "intent": plan.intent.value, "status": TransactionStatus.COMMITTED.value,
        "total_amount": str(plan.total), "received_amount": str(plan.received),
        "credit_amount": str(plan.credit), "party_id": refs.party.id if refs.party else None,
        "party_balance": str(new_party_balance) if new_party_balance is not None else None,
        "idempotent_replay": False,
    }
    writes.append(("update", refs.candidate, {
        "posting_status": PostingStatus.POSTED.value, "confirmed_at": now, "confirmed_by": confirmed_by,
        "confirmation_request_id": request_id, "committed_txn_id": txn_id, "hold_reasons": [],
        "commit_result": result,
        "status_history": firestore.ArrayUnion([{"at": now, "from": posting, "to": PostingStatus.POSTED.value, "by": confirmed_by, "note": "committed"}]),
    }))
    writes.append(("create", refs.confirmation, {
        "request_id": request_id, "merchant_id": refs.merchant_id, "candidate_id": refs.candidate_id,
        "txn_id": txn_id, "confirmed_by": confirmed_by, "confirmed_at": now, "result": result,
    }))

    # ---------------- WRITES ----------------
    _apply_writes(transaction, writes)
    return {"kind": "POSTED", "result": result}


# ==============================================================================
# cancel_candidate
# ==============================================================================

def cancel_candidate(
    merchant_id: str,
    candidate_id: str,
    *,
    cancelled_by: str,
    reason: Optional[str] = None,
    db: Optional[firestore.Client] = None,
    now: Optional[datetime] = None,
) -> CandidateStatus:
    """Cancel an unposted candidate. Idempotent for CANCELLED. POSTED candidates cannot be
    cancelled (committed records are never silently changed); REJECTED/FAILED stay as-is."""
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    candidate_id = _require_str("candidate_id", candidate_id)
    cancelled_by = _require_str("cancelled_by", cancelled_by)
    now = now or datetime.now(timezone.utc)
    _require_merchant(db, merchant_id, actor=cancelled_by)
    ref = _col(db, merchant_id, COL_CANDIDATES).document(candidate_id)

    @firestore.transactional
    def _txn(transaction):
        snap = ref.get(transaction=transaction)
        if not snap.exists or (snap.to_dict() or {}).get("merchant_id") != merchant_id:
            return ("NOT_FOUND", None, None)
        d = snap.to_dict()
        st = d.get("posting_status")
        if st == PostingStatus.CANCELLED.value:
            return ("OK", st, d.get("committed_txn_id"))
        if st not in (PostingStatus.PENDING_CONFIRMATION.value, PostingStatus.NEEDS_REVIEW.value):
            return ("INVALID", st, d.get("committed_txn_id"))
        transaction.update(ref, {
            "posting_status": PostingStatus.CANCELLED.value, "cancelled_at": now,
            "cancelled_by": cancelled_by, "cancel_reason": reason,
            "status_history": firestore.ArrayUnion([{"at": now, "from": st, "to": "CANCELLED", "by": cancelled_by, "note": reason or "cancelled"}]),
        })
        return ("OK", PostingStatus.CANCELLED.value, None)

    outcome, st, txn_id = _run_txn(db, _txn)
    if outcome == "NOT_FOUND":
        raise CandidateNotFoundError(f"Candidate {candidate_id} not found for merchant")
    if outcome == "INVALID":
        raise InvalidStateError(f"Cannot cancel a candidate in state {st}")
    return CandidateStatus(candidate_id=candidate_id, merchant_id=merchant_id,
                           posting_status=PostingStatus(st), committed_txn_id=txn_id)


# ==============================================================================
# review_candidate
# ==============================================================================

def review_candidate(
    merchant_id: str,
    candidate_id: str,
    *,
    reviewed_by: str,
    corrected_candidate: Any,
    review_note: str,
    expected_snapshot_sha256: Optional[str] = None,
    db: Optional[firestore.Client] = None,
    now: Optional[datetime] = None,
) -> StagedCandidate:
    """
    Atomically replace a staged candidate's effective snapshot with a merchant-reviewed one.

    * Only an authorized merchant phone may review (NotAuthorizedError otherwise).
    * Allowed from PENDING_CONFIRMATION or NEEDS_REVIEW. Never from POSTED/CANCELLED/REJECTED
      (REJECTED candidates must be re-submitted as a new source event).
    * The corrected candidate is fully revalidated server-side (BusinessValidator + ledger plan).
      Fundamental arithmetic errors (REJECTED) are refused: the snapshot is left unchanged,
      the refused attempt is appended to review_history, and ValidationFailedError is raised.
    * If revalidation passes cleanly the candidate becomes PENDING_CONFIRMATION with
      review_resolved=True; if only warnings/holds remain it stays NEEDS_REVIEW with the new
      reasons (the corrected snapshot is still recorded).
    * ``extracted_snapshot``/``snapshot_sha256`` always hold the current effective snapshot
      (the revision a confirmation must reference). The first extraction is preserved in
      ``original_extraction`` and every superseded snapshot is kept in ``review_history``.
    * expected_snapshot_sha256 guards against reviewing a revision the caller never saw.
    """
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    candidate_id = _require_str("candidate_id", candidate_id)
    reviewed_by = _require_str("reviewed_by", reviewed_by)
    note = _require_str("review_note", review_note)
    now = now or datetime.now(timezone.utc)
    merchant = _require_merchant(db, merchant_id, actor=reviewed_by)
    corrected = (corrected_candidate.model_copy(deep=True) if isinstance(corrected_candidate, TransactionCandidate)
                 else TransactionCandidate.model_validate(corrected_candidate))
    status, warnings, posting, reasons = _assess(corrected, merchant)
    new_snapshot = corrected.model_dump(mode="json")
    new_sha = _snapshot_hash(new_snapshot)
    ref = _col(db, merchant_id, COL_CANDIDATES).document(candidate_id)

    @firestore.transactional
    def _txn(transaction):
        snap = ref.get(transaction=transaction)
        if not snap.exists or (snap.to_dict() or {}).get("merchant_id") != merchant_id:
            return {"kind": "NOT_FOUND"}
        d = snap.to_dict()
        cur = d.get("posting_status")
        if cur not in (PostingStatus.PENDING_CONFIRMATION.value, PostingStatus.NEEDS_REVIEW.value):
            return {"kind": "INVALID", "reason": f"Candidate is {cur}; only PENDING_CONFIRMATION/NEEDS_REVIEW candidates can be reviewed"}
        if d.get("expires_at") and d["expires_at"] < now:
            return {"kind": "STALE", "reason": "Candidate expired before review"}
        old_snapshot, old_sha = d["extracted_snapshot"], d.get("snapshot_sha256")
        if _snapshot_hash(old_snapshot) != old_sha:
            return {"kind": "INVALID", "reason": "Snapshot integrity check failed (snapshot was modified)"}
        if expected_snapshot_sha256 and expected_snapshot_sha256 != old_sha:
            return {"kind": "STALE", "reason": "Review refers to a different revision than the one staged"}
        entry = {
            "at": now, "by": reviewed_by, "note": note[:1000], "from_sha256": old_sha, "to_sha256": new_sha,
            "previous_snapshot": old_snapshot, "previous_posting_status": cur,
            "outcome": "REFUSED" if posting == PostingStatus.REJECTED else posting.value,
            "reasons": reasons[:20],
        }
        if posting == PostingStatus.REJECTED:
            transaction.update(ref, {"review_history": firestore.ArrayUnion([entry])})
            return {"kind": "REFUSED", "reasons": reasons}
        update = {
            "extracted_snapshot": new_snapshot, "snapshot_sha256": new_sha,
            "transaction_type": corrected.transaction_type.value,
            "validation_status": status, "validation_warnings": warnings,
            "posting_status": posting.value, "hold_reasons": reasons,
            "hold_source": None if posting == PostingStatus.PENDING_CONFIRMATION else "EXTRACTION",
            "review_resolved": posting == PostingStatus.PENDING_CONFIRMATION,
            "review_count": int(d.get("review_count", 0)) + 1,
            "reviewed_at": now, "reviewed_by": reviewed_by,
            "review_history": firestore.ArrayUnion([entry]),
            "status_history": firestore.ArrayUnion([{"at": now, "from": cur, "to": posting.value, "by": reviewed_by, "note": "reviewed: " + note[:200]}]),
        }
        if d.get("original_extraction") is None:      # candidates staged before review support existed
            update["original_extraction"] = old_snapshot
            update["original_snapshot_sha256"] = old_sha
            update["originally_needs_review"] = d.get("validation_status") == "NEEDS_REVIEW"
        transaction.update(ref, update)
        return {"kind": "OK", "expires_at": d.get("expires_at")}

    out = _run_txn(db, _txn)
    k = out["kind"]
    if k == "NOT_FOUND":
        raise CandidateNotFoundError(f"Candidate {candidate_id} not found for merchant")
    if k == "INVALID":
        raise InvalidStateError(out["reason"])
    if k == "STALE":
        raise StaleConfirmationError(out["reason"])
    if k == "REFUSED":
        raise ValidationFailedError(out["reasons"])
    return StagedCandidate(
        candidate_id=candidate_id, merchant_id=merchant_id,
        source_message_id=(ref.get().to_dict() or {}).get("source_message_id", ""),
        validation_status=status, posting_status=posting, hold_reasons=reasons,
        validation_warnings=warnings, already_staged=True, expires_at=out.get("expires_at"),
    )


# ==============================================================================
# Opening balances (explicit postings for receivables that pre-date the ledger)
# ==============================================================================

def post_opening_balance(
    merchant_id: str,
    party_id: str,
    *,
    posted_by: str,
    db: Optional[firestore.Client] = None,
    now: Optional[datetime] = None,
) -> OpeningBalanceResult:
    """
    Post an explicit opening-balance journal (Dr ACCOUNTS_RECEIVABLE / Cr OPENING_BALANCE_EQUITY)
    for a party balance that exists without a journal behind it (e.g. seeded data).

    * Does not change the party's balance and never rewrites history: it appends a journal entry
      and an ``opening_balances/{party_id}`` record, once per party (idempotent replay).
    * If an opening record already exists but the party balance is again un-backed, raises
      InvalidStateError: that discrepancy needs a manual, audited adjustment.
    * Collections against a party are held until its legacy balance is backed by this posting.
    """
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    party_id = _require_str("party_id", party_id)
    if "/" in party_id:
        raise ValueError("invalid party_id")
    posted_by = _require_str("posted_by", posted_by)
    now = now or datetime.now(timezone.utc)
    _require_merchant(db, merchant_id, actor=posted_by)
    m = _mref(db, merchant_id)
    party_ref = m.collection(COL_PARTIES).document(party_id)
    open_ref = m.collection(COL_OPENING).document(party_id)
    ar_ref = m.collection(COL_ACCOUNTS).document(ACC_AR)
    obe_ref = m.collection(COL_ACCOUNTS).document(ACC_OPENING)
    jrnl_ref = m.collection(COL_JOURNAL).document(f"jrnl_open_{party_id}")

    @firestore.transactional
    def _txn(transaction):
        p, o, ar, obe = (s for s in transaction.get_all([party_ref, open_ref, ar_ref, obe_ref]))
        if not p.exists:
            return ("NO_PARTY", None)
        pd = p.to_dict()
        bal_minor = to_minor(_party_balance(pd))
        unposted = bal_minor - int(pd.get("ar_posted_minor", 0))
        if o.exists:
            if unposted != 0:
                return ("DISCREPANCY", unposted)
            return ("REPLAY", o.to_dict()["amount_minor"])
        if unposted < 0:
            return ("DISCREPANCY", unposted)
        if unposted == 0:
            return ("NOTHING", 0)
        lines = [
            {"account": ACC_AR, "debit_minor": unposted, "credit_minor": 0},
            {"account": ACC_OPENING, "debit_minor": 0, "credit_minor": unposted},
        ]
        a, b = _acct_data(ar, ACC_AR), _acct_data(obe, ACC_OPENING)
        transaction.create(jrnl_ref, {
            "journal_id": jrnl_ref.id, "merchant_id": merchant_id, "txn_id": None, "kind": "OPENING_BALANCE",
            "party_id": party_id, "lines": lines, "total_debit_minor": unposted, "total_credit_minor": unposted,
            "business_date": now.astimezone(IST).date().isoformat(), "created_at": now,
        })
        transaction.create(open_ref, {
            "party_id": party_id, "amount_minor": unposted, "posted_by": posted_by, "posted_at": now,
            "journal_id": jrnl_ref.id, "source": "LEGACY_PARTY_BALANCE",
        })
        transaction.set(ar_ref, {"code": ACC_AR, "debit_total_minor": a["debit"] + unposted, "credit_total_minor": a["credit"],
                                 "balance_minor": a["debit"] + unposted - a["credit"], "updated_at": now})
        transaction.set(obe_ref, {"code": ACC_OPENING, "debit_total_minor": b["debit"], "credit_total_minor": b["credit"] + unposted,
                                  "balance_minor": b["debit"] - b["credit"] - unposted, "updated_at": now})
        transaction.update(party_ref, {"udhaar_balance_minor": bal_minor, "ar_posted_minor": bal_minor})
        return ("POSTED", unposted)

    kind, minor = _run_txn(db, _txn)
    if kind == "NO_PARTY":
        raise PartyNotFoundError(f"Party {party_id} not found")
    if kind == "DISCREPANCY":
        raise InvalidStateError(
            f"Party {party_id} balance differs from its journal-backed receivable by {minor} paise after an "
            "opening posting; manual audited adjustment required")
    return OpeningBalanceResult(party_id=party_id, amount=from_minor(minor), journal_id=jrnl_ref.id if minor else None,
                                already_posted=kind in ("REPLAY", "NOTHING"))


def post_opening_balances(merchant_id: str, *, posted_by: str, db: Optional[firestore.Client] = None,
                          now: Optional[datetime] = None) -> List[OpeningBalanceResult]:
    """Post opening balances for every party whose balance is not yet journal-backed."""
    db = db or get_db()
    out = []
    for s in _col(db, _require_str("merchant_id", merchant_id), COL_PARTIES).stream():
        d = s.to_dict() or {}
        if to_minor(_party_balance(d)) != int(d.get("ar_posted_minor", 0)):
            out.append(post_opening_balance(merchant_id, s.id, posted_by=posted_by, db=db, now=now))
    return out


def get_receivables_reconciliation(merchant_id: str, *, db: Optional[firestore.Client] = None) -> ReceivablesReconciliation:
    """Compare the AR control account with party sub-ledger balances (canonical DB state)."""
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    total = backed = 0
    for s in _col(db, merchant_id, COL_PARTIES).stream():
        d = s.to_dict() or {}
        total += to_minor(_party_balance(d))
        backed += int(d.get("ar_posted_minor", 0))
    ar = _col(db, merchant_id, COL_ACCOUNTS).document(ACC_AR).get()
    ar_minor = int(ar.to_dict().get("balance_minor", 0)) if ar.exists else 0
    return ReceivablesReconciliation(
        merchant_id=merchant_id, ar_account_balance=from_minor(ar_minor), journal_backed_party_total=from_minor(backed),
        party_balance_total=from_minor(total), unposted_opening_total=from_minor(total - backed), consistent=ar_minor == backed)


# ==============================================================================
# Reads (canonical DB state; merchant-scoped)
# ==============================================================================

def get_transaction(merchant_id: str, txn_id: str, *, db: Optional[firestore.Client] = None) -> Dict[str, Any]:
    """Return the committed transaction document (Transaction fields + ledger extras)."""
    db = db or get_db()
    snap = _col(db, _require_str("merchant_id", merchant_id), COL_TRANSACTIONS).document(_require_str("txn_id", txn_id)).get()
    if not snap.exists:
        raise TransactionNotFoundError(f"Transaction {txn_id} not found")
    return snap.to_dict()


def list_transactions(
    merchant_id: str,
    *,
    intent: Optional[TransactionIntent] = None,
    party_id: Optional[str] = None,
    business_date: Optional[str] = None,
    limit: int = 50,
    db: Optional[firestore.Client] = None,
) -> List[Dict[str, Any]]:
    """Committed transactions, newest first. Filters are applied server-side by a single
    equality (business_date) plus in-process filtering for intent/party, so no composite
    indexes are required."""
    db = db or get_db()
    q = _col(db, _require_str("merchant_id", merchant_id), COL_TRANSACTIONS)
    query = q.where(filter=firestore.FieldFilter("business_date", "==", business_date)) if business_date else q
    out: List[Dict[str, Any]] = []
    intent_v = TransactionIntent(intent).value if intent else None
    docs = sorted((s.to_dict() for s in query.stream()), key=lambda d: d["committed_at"], reverse=True)
    for d in docs:
        if intent_v and d.get("intent") != intent_v:
            continue
        if party_id and d.get("party_id") != party_id:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def get_party_balance(
    merchant_id: str,
    party_id: Optional[str] = None,
    *,
    party_name: Optional[str] = None,
    db: Optional[firestore.Client] = None,
) -> PartyBalance:
    """Outstanding receivable for a customer, from the canonical party document.
    Provide party_id, or party_name (exact normalized match; ambiguity raises PartyNotFoundError)."""
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    col = _col(db, merchant_id, COL_PARTIES)
    if party_id:
        snap = col.document(party_id).get()
        if not snap.exists:
            raise PartyNotFoundError(f"Party {party_id} not found")
        pid, d = snap.id, snap.to_dict()
    elif party_name:
        docs = {s.id: s.to_dict() for s in col.stream()}
        try:
            pid = _resolve_one(party_name, docs, ("display_name", "aliases", "party_id"), "customer")
        except _Hold as h:
            raise PartyNotFoundError(str(h))
        d = docs[pid]
    else:
        raise ValueError("party_id or party_name is required")
    party = Party.model_validate({**d, "party_id": pid})
    bal = _party_balance(d)
    limit = to_money(party.credit_limit)
    return PartyBalance(party_id=pid, display_name=party.display_name, outstanding=bal, credit_limit=limit,
                        available_credit=limit - bal, last_txn_at=party.last_txn_at)


def get_inventory(
    merchant_id: str,
    sku_id: Optional[str] = None,
    *,
    db: Optional[firestore.Client] = None,
) -> List[InventoryStock]:
    """Stock levels from the canonical inventory documents (one SKU or all)."""
    db = db or get_db()
    col = _col(db, _require_str("merchant_id", merchant_id), COL_INVENTORY)
    if sku_id:
        s = col.document(sku_id).get()
        if not s.exists:
            raise InventoryNotFoundError(f"SKU {sku_id} not found")
        docs = [(s.id, s.to_dict())]
    else:
        docs = [(s.id, s.to_dict()) for s in col.stream()]
    out = []
    for sid, d in sorted(docs):
        item = InventoryItem.model_validate({**d, "sku_id": sid})
        stock, reorder = _stock(d), to_qty(item.reorder_level)
        out.append(InventoryStock(
            sku_id=sid, item_name=item.item_name, unit=item.unit, current_stock=stock,
            default_unit_price=to_money(item.default_unit_price), reorder_level=reorder,
            below_reorder_level=stock <= reorder,
        ))
    return out


def get_daily_summary(
    merchant_id: str,
    business_date: Optional[str] = None,
    *,
    db: Optional[firestore.Client] = None,
) -> DailySummary:
    """Totals for one IST business day, computed from committed transactions."""
    db = db or get_db()
    merchant_id = _require_str("merchant_id", merchant_id)
    day = business_date or datetime.now(IST).date().isoformat()
    date.fromisoformat(day)  # validate
    txns = list(_col(db, merchant_id, COL_TRANSACTIONS)
                .where(filter=firestore.FieldFilter("business_date", "==", day)).stream())
    z = Decimal("0.00")
    sales_total = cash = upi = bank = credit = coll = z
    sales_n = 0
    produced = Decimal("0.000")
    for s in txns:
        d = s.to_dict()
        if d.get("status") != TransactionStatus.COMMITTED.value:
            continue
        if d["intent"] == TransactionIntent.SALE_INVOICE.value:
            sales_n += 1
            sales_total += from_minor(d["total_minor"])
            credit += from_minor(d["credit_minor"])
        elif d["intent"] == TransactionIntent.UDHAAR_COLLECTION.value:
            coll += from_minor(d["received_minor"])
        elif d["intent"] == TransactionIntent.PRODUCTION_LOG.value:
            produced += sum((to_qty(l["quantity"]) for l in d.get("line_items", [])), Decimal("0.000"))
    for p in _col(db, merchant_id, COL_PAYMENTS).where(filter=firestore.FieldFilter("business_date", "==", day)).stream():
        pd = p.to_dict()
        amt = from_minor(pd["amount_minor"])
        if pd["account"] == ACC_CASH:
            cash += amt
        elif pd["account"] == ACC_UPI:
            upi += amt
        elif pd["account"] == ACC_BANK:
            bank += amt
    ar = _col(db, merchant_id, COL_ACCOUNTS).document(ACC_AR).get()
    ar_out = from_minor(ar.to_dict().get("balance_minor", 0)) if ar.exists else z
    return DailySummary(
        merchant_id=merchant_id, business_date=day, transaction_count=len(txns), sales_count=sales_n,
        sales_total=sales_total, cash_received=cash, upi_received=upi, bank_received=bank,
        credit_extended=credit, collections_total=coll, units_produced=produced,
        total_receivables_outstanding=ar_out,
    )


# Backend-neutral read contracts used by the dashboard during SQL migration.
def get_candidate_record(merchant_id, candidate_id, *, db=None):
    snap = _col(db or get_db(), merchant_id, COL_CANDIDATES).document(candidate_id).get()
    row = snap.to_dict() if snap.exists else None
    if not row or row.get("merchant_id") != merchant_id:
        raise CandidateNotFoundError("Candidate unavailable")
    return row


def list_candidates(merchant_id, *, db=None):
    rows = _col(db or get_db(), merchant_id, COL_CANDIDATES).order_by("created_at", direction=firestore.Query.DESCENDING).limit(30).stream()
    return [{**r.to_dict(),"candidate_id":r.id,"revision":r.to_dict().get("snapshot_sha256")} for r in rows]


def list_parties(merchant_id, *, db=None):
    rows = _col(db or get_db(), merchant_id, COL_PARTIES).stream()
    return [{"party_id":r.id,"display_name":r.to_dict().get("display_name",r.id),"phone":r.to_dict().get("phone"),
        "current_udhaar_balance":_party_balance(r.to_dict()),"credit_limit":to_money(r.to_dict().get("credit_limit",50000))} for r in rows]
