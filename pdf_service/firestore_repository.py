"""Read Claude's committed transactions and own only document artifact metadata."""
from datetime import date
from decimal import Decimal
from google.cloud import firestore
from pdf_service.generate_invoice import CommittedSale, InvoiceArtifact


class FirestoreInvoiceRepository:
    def __init__(self, db=None, ledger_module=None):
        if ledger_module is None:
            from app.services import ledger as ledger_module
        self.ledger = ledger_module
        self.db = db if db is not None else self.ledger.get_db()

    def _merchant(self, merchant_id):
        if not merchant_id or "/" in merchant_id:
            raise ValueError("Invalid merchant ID")
        return self.db.collection("merchants").document(merchant_id)

    def _transaction(self, merchant_id, transaction_id):
        if not transaction_id or "/" in transaction_id:
            raise ValueError("Invalid transaction ID")
        return self._merchant(merchant_id).collection("transactions").document(transaction_id)

    def get_committed_sale(self, merchant_id, transaction_id):
        self._transaction(merchant_id, transaction_id)  # validate path components first
        tx = self.ledger.get_transaction(merchant_id, transaction_id, db=self.db)
        if (tx.get("status") != "COMMITTED" or tx.get("intent") != "SALE_INVOICE"
                or tx.get("merchant_id") != merchant_id or tx.get("txn_id") != transaction_id):
            raise ValueError("A committed sale belonging to the merchant is required")
        merchant = self._merchant(merchant_id).get().to_dict() or {}
        # v1 ledger documents are explicitly denominated in INR. Future versions
        # must persist currency; never infer USD from source/model text.
        currency = tx.get("currency")
        if currency is None:
            if tx.get("ledger_version") != "1.0":
                raise ValueError("Committed currency is missing")
            currency = "INR"
        def amount(key):
            return Decimal(tx[key]) / 100
        items = [dict(name=i["item_name"], quantity=str(i["quantity"]), unit=i["unit"],
                      unit_price=str(i["unit_price"]), discount=str(i.get("discount", 0)),
                      line_total=str(i["subtotal"])) for i in tx["line_items"]]
        subtotal = sum((Decimal(i["line_total"]) for i in items), Decimal(0))
        tax, total = amount("tax_minor"), amount("total_minor")
        # Reconcile committed amounts; do not recompute or repair a ledger total.
        discount = subtotal + tax - total
        return CommittedSale(
            merchant_id=merchant_id, transaction_id=transaction_id, status=tx["status"], intent=tx["intent"],
            merchant_name=merchant["business_name"], merchant_address=merchant.get("address", ""),
            buyer_name=tx["party_name"], buyer_address=tx.get("buyer_address", ""),
            invoice_number=tx.get("invoice_number") or transaction_id,
            invoice_date=date.fromisoformat(tx["business_date"]), currency=currency, items=items,
            subtotal=subtotal, discount=discount, tax=tax, total=total,
            paid=amount("received_minor"), due=amount("credit_minor"))

    def associate_invoice(self, merchant_id, transaction_id, artifact):
        if artifact.merchant_id != merchant_id or artifact.transaction_id != transaction_id:
            raise ValueError("Artifact ownership mismatch")
        sale_ref = self._transaction(merchant_id, transaction_id)
        artifact_ref = sale_ref.collection("invoice_artifacts").document("basic_v1")

        @firestore.transactional
        def attach(transaction):
            # Only database reads/writes here. No rendering, object upload or Meta calls.
            sale = sale_ref.get(transaction=transaction)
            existing = artifact_ref.get(transaction=transaction)
            d = sale.to_dict() or {}
            if d.get("merchant_id") != merchant_id or d.get("status") != "COMMITTED" or d.get("intent") != "SALE_INVOICE":
                raise ValueError("Committed sale no longer available")
            if existing.exists:
                return InvoiceArtifact.model_validate(existing.to_dict())
            transaction.create(artifact_ref, artifact.model_dump())
            return artifact
        return attach(self.db.transaction())
