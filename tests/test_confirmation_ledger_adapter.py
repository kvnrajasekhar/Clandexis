"""Translate Claude's public API; this is not an alternative ledger implementation."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from decimal import Decimal
import pytest
from app.services.confirmation import ConfirmationError
from app.services.confirmation_ledger_adapter import ClaudeLedgerAdapter
from tests.test_confirmation import candidate, NOW
from pdf_service.firestore_repository import FirestoreInvoiceRepository


def adapter_fixture():
    doc = dict(merchant_id="merchant", extracted_snapshot=candidate().model_dump(),
               posting_status="PENDING_CONFIRMATION", snapshot_sha256="snapshot-hash",
               expires_at=NOW + timedelta(minutes=30), validation_status="ACCEPTED")
    db = Mock()
    ref = db.collection.return_value.document.return_value.collection.return_value.document.return_value
    ref.get.return_value = SimpleNamespace(exists=True, to_dict=lambda: doc)
    class LedgerError(Exception):
        pass
    api = SimpleNamespace(LedgerError=LedgerError, stage_candidate=Mock(return_value=SimpleNamespace(candidate_id="persisted")),
        confirm_and_commit=Mock(return_value=SimpleNamespace(merchant_id="merchant", status="COMMITTED",
            txn_id="txn1", received_amount=Decimal("73"), credit_amount=Decimal("27"))),
        cancel_candidate=Mock(return_value=SimpleNamespace(posting_status=SimpleNamespace(value="CANCELLED"))),
        get_transaction=Mock(return_value=dict(movement_ids=[])))
    return ClaudeLedgerAdapter(db, api), api, db, doc


def test_adapter_uses_actual_id_and_snapshot_guard():
    adapter, api, db, doc = adapter_fixture()
    row = adapter.stage_candidate("merchant", "original", 0, candidate(), "ignored", doc["expires_at"])
    assert row.candidate_id == "persisted" and row.revision == "snapshot-hash"
    result = adapter.confirm_candidate("merchant", row.candidate_id, "wamid.callback", row.revision, NOW)
    api.confirm_and_commit.assert_called_once_with("merchant", "persisted", confirmed_by="merchant",
        confirmation_request_id="wamid.callback", expected_snapshot_sha256="snapshot-hash", db=db, now=NOW)
    assert result.transaction_id == "txn1" and result.amount_paid == 73
    assert adapter.cancel_candidate("merchant", "persisted", "cancel-event", row.revision, NOW) == "CANCELLED"


def test_adapter_reads_posting_movement_balance():
    adapter, api, _, doc = adapter_fixture()
    api.get_transaction.return_value = dict(movement_ids=["mv1"])
    doc.update(reference_txn_id="txn1", sku_id="shirt", balance_after=18)
    result = adapter.confirm_candidate("merchant", "persisted", "callback", "snapshot-hash", NOW)
    assert result.inventory_balance == {"shirt": 18}


def test_missing_review_extension_fails_closed():
    adapter, api, _, _ = adapter_fixture()
    with pytest.raises(ConfirmationError, match="review support is unavailable"):
        adapter.replace_reviewed_candidate("merchant", "persisted", "hash", candidate(), "Checked", NOW)
    api.confirm_and_commit.assert_not_called()


@pytest.mark.parametrize("status,expected", [("POSTED", "COMMITTED"), ("CANCELLED", "CANCELLED"),
    ("NEEDS_REVIEW", "AWAITING_CONFIRMATION"), ("REJECTED", "REJECTED")])
def test_maps_ledger_status(status, expected):
    adapter, _, _, doc = adapter_fixture()
    doc["posting_status"] = status
    assert adapter.get_candidate("merchant", "persisted").status == expected


def test_invoice_reader_uses_committed_minor_units():
    api = SimpleNamespace(get_transaction=Mock(return_value=dict(
        merchant_id="merchant", txn_id="txn1", status="COMMITTED", intent="SALE_INVOICE", ledger_version="1.0",
        party_name="Ravi", business_date="2026-11-20", total_minor=10000, received_minor=7300,
        credit_minor=2700, tax_minor=0, line_items=[dict(item_name="Shirt", quantity=2,
        unit_price=50, unit="pieces", subtotal=100)])))
    db = Mock()
    db.collection.return_value.document.return_value.get.return_value.to_dict.return_value = {"business_name": "Merchant"}
    repo = FirestoreInvoiceRepository(db, api)
    sale = repo.get_committed_sale("merchant", "txn1")
    assert (sale.total, sale.paid, sale.due) == (100, 73, 27)
    assert sale.currency == "INR" and sale.invoice_number == "txn1"
    api.get_transaction.return_value["status"] = "AWAITING_CONFIRMATION"
    with pytest.raises(ValueError):
        repo.get_committed_sale("merchant", "txn1")
