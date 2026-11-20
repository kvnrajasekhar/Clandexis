from datetime import date
import pytest
from pydantic import ValidationError
from pypdf import PdfReader
from app.services.confirmation import AuthenticatedMerchant
from pdf_service import CommittedSale, InvoiceService, LocalArtifactStore
from pdf_service.generate_invoice import render_invoice


def committed_sale(currency="INR", count=3, **changes):
    return CommittedSale(**(dict(
        merchant_id="merchant", transaction_id="txn_db_1", status="COMMITTED", intent="SALE_INVOICE",
        merchant_name="Sri Balaji Textiles " * 8, merchant_address="12 Market Street, Hyderabad",
        buyer_name="Ravi & Sons <Wholesale> International Trading Company " * 5,
        buyer_address="46 Long Road, New Delhi", invoice_number="INV-2026-0042",
        invoice_date=date(2026, 11, 20), currency=currency,
        items=tuple(dict(name=f"Item {n}: Cotton premium fabric with a deliberately long description " * 3,
                         quantity=2, unit_price="50.00", discount="0.00", line_total="100.00")
                    for n in range(count)),
        subtotal=100 * count, discount="10.00", tax="5.00", total=100 * count - 5,
        paid="73.00", due=100 * count - 78) | changes))


class InvoiceRepoStub:
    def __init__(self, sale):
        self.sale = sale
        self.attached = None
        self.fail_association = False

    def get_committed_sale(self, merchant_id, transaction_id):
        return self.sale

    def associate_invoice(self, merchant_id, transaction_id, artifact):
        if self.fail_association:
            raise OSError("Database association unavailable")
        if self.attached is None:
            self.attached = artifact
        return self.attached


@pytest.mark.parametrize("currency", ["INR", "USD"])
def test_invoice_committed_totals_and_wrapped_multiple_pages(tmp_path, currency):
    sale = committed_sale(currency, 32)
    service = InvoiceService(InvoiceRepoStub(sale), LocalArtifactStore(tmp_path))
    artifact = service.generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    reader = PdfReader(tmp_path / artifact.storage_key)
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 3
    assert "BASIC INVOICE" in text and "Not a GST tax invoice" in text
    assert "INV-2026-0042" in text and "2026-11-20" in text
    assert "Ravi & Sons <Wholesale>" in text
    for amount in (sale.subtotal, sale.discount, sale.tax, sale.total, sale.paid, sale.due):
        assert f"{currency} {amount:,.2f}" in text
    assert "Item 31" in text
    assert "Quantity" in text and "Unit price" in text


def test_pdf_retry_is_identical_and_associated_once(tmp_path):
    repo = InvoiceRepoStub(committed_sale())
    service = InvoiceService(repo, LocalArtifactStore(tmp_path))
    first = service.generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    second = service.generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    assert first == second
    assert len(list(tmp_path.rglob("*.pdf"))) == 1
    assert render_invoice(repo.sale) == render_invoice(repo.sale)


@pytest.mark.parametrize("changes", [dict(status="AWAITING_CONFIRMATION"), dict(intent="PURCHASE_ENTRY"),
    dict(total="999.00"), dict(paid="9999.00"), dict(subtotal="1.00"),
    dict(total="NaN"), dict(total="295.001")])
def test_rejects_uncommitted_or_inconsistent_snapshot(changes):
    with pytest.raises(ValidationError):
        committed_sale(**changes)


def test_pdf_checks_owner_and_transaction(tmp_path):
    service = InvoiceService(InvoiceRepoStub(committed_sale()), LocalArtifactStore(tmp_path))
    with pytest.raises(ValueError):
        service.generate_document(AuthenticatedMerchant("other"), "txn_db_1")
    with pytest.raises(ValueError):
        service.generate_document(AuthenticatedMerchant("merchant"), "other")
    assert not list(tmp_path.rglob("*.pdf"))


def test_failure_does_not_undo_commit_and_retry_recovers(tmp_path):
    repo = InvoiceRepoStub(committed_sale())
    service = InvoiceService(repo, LocalArtifactStore(tmp_path))
    repo.fail_association = True
    with pytest.raises(OSError):
        service.generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    assert repo.sale.status == "COMMITTED"
    repo.fail_association = False
    service.generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    assert len(list(tmp_path.rglob("*.pdf"))) == 1


def test_storage_failure_does_not_associate_or_change_sale():
    repo = InvoiceRepoStub(committed_sale())
    class BrokenStore:
        def put_if_absent(self, key, content):
            raise OSError("Storage unavailable")
    with pytest.raises(OSError):
        InvoiceService(repo, BrokenStore()).generate_document(AuthenticatedMerchant("merchant"), "txn_db_1")
    assert repo.sale.status == "COMMITTED" and repo.attached is None


def test_firestore_association_attaches_once_after_committed_read(monkeypatch):
    from unittest.mock import Mock
    from pdf_service import InvoiceArtifact
    from pdf_service import firestore_repository as module
    monkeypatch.setattr(module.firestore, "transactional", lambda callback: callback)
    db = Mock()
    sale_ref = db.collection.return_value.document.return_value.collection.return_value.document.return_value
    artifact_ref = sale_ref.collection.return_value.document.return_value
    sale_ref.get.return_value.to_dict.return_value = dict(
        merchant_id="merchant", status="COMMITTED", intent="SALE_INVOICE")
    artifact_ref.get.return_value.exists = False
    artifact = InvoiceArtifact(merchant_id="merchant", transaction_id="txn1",
        storage_key="key.pdf", sha256="hash", filename="invoice.pdf")
    repo = module.FirestoreInvoiceRepository(db, ledger_module=object())
    assert repo.associate_invoice("merchant", "txn1", artifact) == artifact
    db.transaction.return_value.create.assert_called_once_with(artifact_ref, artifact.model_dump())
    artifact_ref.get.return_value.exists = True
    artifact_ref.get.return_value.to_dict.return_value = artifact.model_dump()
    assert repo.associate_invoice("merchant", "txn1", artifact) == artifact
    assert db.transaction.return_value.create.call_count == 1


def test_firestore_association_rejects_uncommitted_sale(monkeypatch):
    from unittest.mock import Mock
    from pdf_service import InvoiceArtifact
    from pdf_service import firestore_repository as module
    monkeypatch.setattr(module.firestore, "transactional", lambda callback: callback)
    db = Mock()
    sale_ref = db.collection.return_value.document.return_value.collection.return_value.document.return_value
    sale_ref.get.return_value.to_dict.return_value = dict(
        merchant_id="merchant", status="AWAITING_CONFIRMATION", intent="SALE_INVOICE")
    repo = module.FirestoreInvoiceRepository(db, ledger_module=object())
    artifact = InvoiceArtifact(merchant_id="merchant", transaction_id="txn1",
        storage_key="key.pdf", sha256="hash", filename="invoice.pdf")
    with pytest.raises(ValueError):
        repo.associate_invoice("merchant", "txn1", artifact)
    db.transaction.return_value.create.assert_not_called()
