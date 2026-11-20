"""PDF facts and retry-safe artifacts from immutable PostgreSQL invoice snapshots."""

from hashlib import sha256
from app.services import postgres_ledger as ledger
from pdf_service.generate_invoice import CommittedSale, InvoiceArtifact


class PostgresInvoiceRepository:
    def __init__(self, db=None):
        self.db = db or ledger.get_db()

    def get_committed_sale(self, merchant_id, transaction_id):
        with self.db.transaction(merchant_id) as conn:
            ledger._merchant(conn, merchant_id)
            row = conn.execute(
                "SELECT * FROM invoices WHERE merchant_id=%s AND transaction_id=%s",
                (merchant_id, transaction_id),
            ).fetchone()
            if not row:
                raise ValueError("Committed sale invoice unavailable")
            return CommittedSale(
                merchant_id=merchant_id,
                transaction_id=transaction_id,
                status="COMMITTED",
                intent="SALE_INVOICE",
                merchant_name=row["merchant_snapshot"]["business_name"],
                buyer_name=row["buyer_snapshot"]["display_name"],
                invoice_number=row["invoice_number"],
                invoice_date=row["invoice_date"],
                currency=row["currency"],
                items=row["items_snapshot"],
                subtotal=row["subtotal"],
                discount=row["discount_amount"],
                tax=row["tax_amount"],
                total=row["total_amount"],
                paid=row["amount_paid"],
                due=row["amount_due"],
            )

    def associate_invoice(self, merchant_id, transaction_id, artifact):
        if (
            artifact.merchant_id != merchant_id
            or artifact.transaction_id != transaction_id
        ):
            raise ValueError("Artifact ownership mismatch")
        with self.db.transaction(merchant_id) as conn:
            row = conn.execute(
                "SELECT id FROM invoices WHERE merchant_id=%s AND transaction_id=%s",
                (merchant_id, transaction_id),
            ).fetchone()
            if not row:
                raise ValueError("Committed invoice unavailable")
            conn.execute(
                """INSERT INTO invoice_artifacts(merchant_id,invoice_id,template_version,generation_status,storage_key,sha256_hash,generated_at)
                VALUES(%s,%s,'basic_v1','GENERATED',%s,%s,now()) ON CONFLICT (merchant_id,invoice_id,template_version) DO NOTHING""",
                (merchant_id, row["id"], artifact.storage_key, artifact.sha256),
            )
        return get_invoice_artifact(merchant_id, transaction_id, self.db)


def get_invoice_artifact(merchant_id, transaction_id, db=None):
    with (db or ledger.get_db()).transaction(merchant_id) as conn:
        row = conn.execute(
            """SELECT a.* FROM invoice_artifacts a JOIN invoices i ON i.merchant_id=a.merchant_id AND i.id=a.invoice_id
            WHERE i.merchant_id=%s AND i.transaction_id=%s AND a.template_version='basic_v1' AND a.generation_status='GENERATED'""",
            (merchant_id, transaction_id),
        ).fetchone()
        if not row:
            return None
        association = sha256(f"{merchant_id}\0{transaction_id}".encode()).hexdigest()
        return InvoiceArtifact(
            merchant_id=merchant_id,
            transaction_id=transaction_id,
            storage_key=row["storage_key"],
            sha256=row["sha256_hash"],
            filename=f"invoice-{association[:16]}.pdf",
        )
