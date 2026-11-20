"""Application confirmation boundary. Persistence/atomic posting belongs to the ledger.

No transport calls, credentials, or Firestore mutations live here. See the interface
contract in docs/CONFIRMATION_PDF_INTERFACE.md before implementing LedgerService.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Callable, Literal, Protocol
from uuid import uuid4

from app.schemas.transaction import TransactionCandidate
from app.services.validator import BusinessValidator


class ConfirmationError(ValueError):
    """Safe user-facing failure; no posting should have occurred."""


@dataclass(frozen=True)
class AuthenticatedMerchant:
    # Construct ONLY after transport authentication and merchant authorization.
    merchant_id: str
    actor_phone: str | None = None


@dataclass(frozen=True)
class StoredCandidate:
    candidate_id: str
    merchant_id: str
    candidate: TransactionCandidate
    revision: int | str
    expires_at: datetime
    status: str = "AWAITING_CONFIRMATION"
    review_resolved: bool = False
    originally_needs_review: bool = False


@dataclass(frozen=True)
class PostingResult:
    transaction_id: str
    amount_paid: Decimal
    credit_outstanding: Decimal
    inventory_balance: dict[str, Decimal]
    posting_status: Literal["COMMITTED"] = "COMMITTED"


class LedgerService(Protocol):
    """All methods tenant-scoped. Mutations must enforce the documented atomic rules."""

    def stage_candidate(self, merchant_id: str, source_event_id: str, index: int,
                        candidate: TransactionCandidate, candidate_id: str,
                        expires_at: datetime) -> StoredCandidate: ...
    def get_candidate(self, merchant_id: str, candidate_id: str) -> StoredCandidate: ...
    def active_candidates(self, merchant_id: str, now: datetime) -> list[StoredCandidate]: ...
    def replace_reviewed_candidate(self, merchant_id: str, candidate_id: str,
                                   expected_revision: int | str, candidate: TransactionCandidate,
                                   review_note: str, now: datetime) -> StoredCandidate: ...
    def confirm_candidate(self, merchant_id: str, candidate_id: str,
                          source_event_id: str, expected_revision: int | str,
                          now: datetime, *, actor_phone: str | None = None) -> PostingResult: ...
    def cancel_candidate(self, merchant_id: str, candidate_id: str,
                         source_event_id: str, expected_revision: int | str,
                         now: datetime, *, actor_phone: str | None = None) -> str: ...


_ID = re.compile(r"^clx:v1:(confirm|cancel):([A-Za-z0-9_-]{1,128})$")


def button_id(candidate_id: str, action: str) -> str:
    value = f"clx:v1:{action}:{candidate_id}"
    if not _ID.fullmatch(value):
        raise ConfirmationError("Invalid confirmation button ID.")
    return value


class ConfirmationService:
    def __init__(self, ledger: LedgerService,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.ledger = ledger
        self.clock = clock

    def _owned(self, merchant: AuthenticatedMerchant, candidate_id: str) -> StoredCandidate:
        if not isinstance(merchant, AuthenticatedMerchant) or not merchant.merchant_id:
            raise ConfirmationError("Authenticated merchant required.")
        row = self.ledger.get_candidate(merchant.merchant_id, candidate_id)
        if row.merchant_id != merchant.merchant_id or row.candidate_id != candidate_id:
            raise ConfirmationError("Candidate unavailable for this merchant.")
        return row

    def stage(self, merchant: AuthenticatedMerchant, event_id: str, index: int,
              candidate: TransactionCandidate) -> StoredCandidate:
        row = self.ledger.stage_candidate(
            merchant.merchant_id, event_id, index, candidate.model_copy(deep=True),
            uuid4().hex, self.clock() + timedelta(minutes=30))
        # A retry must return the already persisted ID, never the newly proposed one.
        self._owned(merchant, row.candidate_id)
        return row

    def parse_reply(self, merchant: AuthenticatedMerchant, message) -> tuple[str, str] | None:
        from models.canonical import MessageType
        if message.message_type in (MessageType.INTERACTIVE_BUTTON, MessageType.INTERACTIVE_LIST):
            if message.interactive_id and (message.interactive_id.startswith("retry_") or message.interactive_id.startswith("LANG_")):
                return None
            match = _ID.fullmatch(message.interactive_id or "")
            if not match:
                raise ConfirmationError("Invalid confirmation button. Request fresh confirmation.")
            return match.group(2), match.group(1)
        text = (message.text_content or "").strip().upper()
        if message.message_type == MessageType.TEXT and text in ("YES", "NO"):
            active = self.ledger.active_candidates(merchant.merchant_id, self.clock())
            if len(active) != 1:
                raise ConfirmationError("YES/NO requires exactly one active candidate. Use its buttons.")
            return active[0].candidate_id, "confirm" if text == "YES" else "cancel"
        return None

    def resolve_review(self, merchant: AuthenticatedMerchant, candidate_id: str,
                       corrected: TransactionCandidate, review_note: str) -> StoredCandidate:
        row = self._owned(merchant, candidate_id)
        self._pending(row)
        if row.candidate.validation_status == "REJECTED":
            raise ConfirmationError("Rejected candidates cannot be posted. Submit a new candidate.")
        if not review_note.strip():
            raise ConfirmationError("Explicit review note required.")
        tx = corrected.model_copy(deep=True)
        # These flags describe the old extraction, not the reviewed facts. Validation
        # must run again and will restore any unresolved deterministic warnings.
        tx.needs_clarification = False
        tx.requires_review = False
        tx.clarification_question = None
        tx = BusinessValidator.validate_candidate(tx)
        self._valid(tx)
        return self.ledger.replace_reviewed_candidate(
            merchant.merchant_id, candidate_id, row.revision, tx, review_note, self.clock())

    def _pending(self, row: StoredCandidate):
        if row.status != "AWAITING_CONFIRMATION":
            raise ConfirmationError(f"Candidate is {row.status}.")
        if row.expires_at <= self.clock():
            raise ConfirmationError("Confirmation expired. Request a fresh candidate.")

    @staticmethod
    def _valid(tx: TransactionCandidate):
        if (tx.validation_status != "ACCEPTED" or tx.needs_clarification
                or tx.requires_review or tx.missing_fields
                or tx.transaction_type in ("unknown", "query")):
            raise ConfirmationError("Candidate needs correction or explicit review and revalidation.")

    def handle_confirmation(self, authenticated_merchant: AuthenticatedMerchant,
                            candidate_id: str, action: str, source_event_id: str):
        if action not in ("confirm", "cancel") or not source_event_id.strip():
            raise ConfirmationError("Invalid confirmation action or source event.")
        row = self._owned(authenticated_merchant, candidate_id)
        # Let the atomic ledger operation replay terminal results. It must reject
        # opposite actions, re-check tenant, expiry/revision and all validation gates.
        if row.status != "AWAITING_CONFIRMATION" and not (
                (row.status == "COMMITTED" and action == "confirm")
                or (row.status == "CANCELLED" and action == "cancel")):
            raise ConfirmationError(f"Candidate is {row.status}.")
        if row.status == "AWAITING_CONFIRMATION":
            self._pending(row)
            if action == "confirm":
                if row.originally_needs_review and not row.review_resolved:
                    raise ConfirmationError("Explicit review and revalidation required.")
                self._valid(row.candidate)
                self._valid(BusinessValidator.validate_candidate(row.candidate.model_copy(deep=True)))
        if action == "cancel":
            return self.ledger.cancel_candidate(authenticated_merchant.merchant_id, candidate_id,
                                                source_event_id, row.revision, self.clock(),
                                              actor_phone=authenticated_merchant.actor_phone)
        result = self.ledger.confirm_candidate(authenticated_merchant.merchant_id, candidate_id,
                                              source_event_id, row.revision, self.clock(),
                                              actor_phone=authenticated_merchant.actor_phone)
        if not isinstance(result, PostingResult) or result.posting_status != "COMMITTED":
            raise RuntimeError("Ledger must return a persisted COMMITTED PostingResult.")
        return result


_service: ConfirmationService | None = None


def configure_confirmation(ledger: LedgerService) -> ConfirmationService:
    global _service
    _service = ConfirmationService(ledger)
    return _service


def get_confirmation_service() -> ConfirmationService:
    if _service is None:
        raise ConfirmationError("Confirmation storage is unavailable. Nothing has been posted.")
    return _service


def handle_confirmation(authenticated_merchant, candidate_id, action, source_event_id):
    return get_confirmation_service().handle_confirmation(
        authenticated_merchant, candidate_id, action, source_event_id)
