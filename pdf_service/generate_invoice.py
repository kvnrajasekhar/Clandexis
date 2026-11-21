"""Committed-sale PDF generation, independent of ledger writes and Meta delivery."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Literal, Protocol
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, model_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

from app.services.confirmation import AuthenticatedMerchant


class InvoiceItem(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    name: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit: str = "pieces"
    unit_price: Decimal = Field(ge=0, decimal_places=2)
    discount: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    line_total: Decimal = Field(ge=0, decimal_places=2)

    @model_validator(mode="after")
    def reconcile(self):
        if self.quantity * self.unit_price - self.discount != self.line_total:
            raise ValueError("Committed item amounts do not reconcile")
        return self


class CommittedSale(BaseModel):
    """Read model supplied from persisted data, never from extractor output.

    These invoice fields are deliberately separate from the existing database
    schema. The ledger adapter must join the committed sale and party snapshots.
    """
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    merchant_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    status: Literal["COMMITTED"]
    intent: Literal["SALE_INVOICE"]
    merchant_name: str = Field(min_length=1)
    merchant_address: str = ""
    buyer_name: str = Field(min_length=1)
    buyer_address: str = ""
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: tuple[InvoiceItem, ...] = Field(min_length=1)
    subtotal: Decimal = Field(ge=0, decimal_places=2)
    discount: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    tax: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    total: Decimal = Field(ge=0, decimal_places=2)
    paid: Decimal = Field(ge=0, decimal_places=2)
    due: Decimal = Field(ge=0, decimal_places=2)

    @model_validator(mode="after")
    def reconcile(self):
        if sum((i.line_total for i in self.items), Decimal(0)) != self.subtotal:
            raise ValueError("Committed invoice subtotal differs from line totals")
        if self.subtotal - self.discount + self.tax != self.total:
            raise ValueError("Committed invoice total does not reconcile")
        if self.paid + self.due != self.total:
            raise ValueError("Committed paid and due differ from total")
        return self


class InvoiceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)
    merchant_id: str
    transaction_id: str
    storage_key: str
    sha256: str
    filename: str
    content_type: Literal["application/pdf"] = "application/pdf"


class InvoiceRepository(Protocol):
    def get_committed_sale(self, merchant_id: str, transaction_id: str) -> CommittedSale: ...
    def associate_invoice(self, merchant_id: str, transaction_id: str,
                          artifact: InvoiceArtifact) -> InvoiceArtifact:
        """Atomically attach once to a COMMITTED sale; return existing artifact on retry."""
        ...


class ArtifactStore(Protocol):
    def put_if_absent(self, key: str, content: bytes) -> None: ...


class LocalArtifactStore:
    """Atomic, immutable local storage for development or a shared durable volume.

    Production deployments should inject a durable object-store implementation.
    This is storage only; it does not implement ledger or database behavior.
    """
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put_if_absent(self, key: str, content: bytes) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}/[a-f0-9]{64}\.pdf", key):
            raise ValueError("Invalid artifact key")
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temp = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, target)  # Atomic publish; readers never see partial PDFs.
            except FileExistsError:
                if target.read_bytes() != content:
                    raise ValueError("Existing artifact content differs")
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def render_invoice(sale: CommittedSale) -> bytes:
    """Render an already verified database read model. Never label it GST compliant."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="InvoiceCell", fontName="Helvetica", fontSize=9,
                              leading=13, splitLongWords=True))
    styles.add(ParagraphStyle(name="InvoiceNumber", parent=styles["InvoiceCell"], alignment=TA_RIGHT))
    def p(value, style="InvoiceCell"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])
    def money(value):
        return f"{sale.currency} {value:,.2f}"
    story = [p("BASIC INVOICE", "Title"),
             p("Not a GST tax invoice. No GST compliance certification."), Spacer(1, 16),
             p(f"Invoice: {sale.invoice_number}", "Heading2"),
             p(f"Date: {sale.invoice_date.isoformat()} | Currency: {sale.currency}"),
             p(f"Committed transaction: {sale.transaction_id}"), Spacer(1, 14),
             p("Merchant", "Heading3"), p(sale.merchant_name), p(sale.merchant_address),
             p("Bill to", "Heading3"), p(sale.buyer_name), p(sale.buyer_address), Spacer(1, 16)]
    rows = [[p(x) for x in ("Item / description", "Quantity", "Unit price", "Discount", "Amount")]]
    for item in sale.items:
        rows.append([p(item.name), p(f"{item.quantity} {item.unit}"),
                     p(money(item.unit_price), "InvoiceNumber"),
                     p(money(item.discount), "InvoiceNumber"),
                     p(money(item.line_total), "InvoiceNumber")])
    table = Table(rows, colWidths=[185, 60, 84, 75, 95], repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5edf4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), .6, colors.HexColor("#8296aa")),
        ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#dce3ea")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([table, Spacer(1, 16)])
    totals = [("Subtotal", sale.subtotal), ("Invoice discount", sale.discount),
              ("Tax (as recorded)", sale.tax), ("Total", sale.total),
              ("Paid", sale.paid), ("Amount due", sale.due)]
    summary = Table([[p(label), p(money(amount), "InvoiceNumber")] for label, amount in totals],
                    colWidths=[350, 149])
    summary.setStyle(TableStyle([("BACKGROUND", (0, 3), (-1, -1), colors.HexColor("#f0f4f8")),
                                ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(KeepTogether(summary))
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=48, rightMargin=48,
                            topMargin=42, bottomMargin=48, title="Basic invoice", author="Clandexis", invariant=1)
    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#52667a"))
        canvas.drawString(48, 26, "Clandexis | Basic invoice from a committed sale")
        canvas.drawRightString(A4[0] - 48, 26, f"Page {document.page}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def render_stock_intake_receipt(
    merchant_name: str,
    transaction_id: str,
    business_date: str,
    items: list,
) -> bytes:
    """Render an official Goods Receipt Note (GRN) / Stock Intake document."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReceiptCell", fontName="Helvetica", fontSize=9, leading=13, splitLongWords=True))
    styles.add(ParagraphStyle(name="ReceiptRight", parent=styles["ReceiptCell"], alignment=TA_RIGHT))
    def p(value, style="ReceiptCell"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    story = [
        p("INVENTORY STOCK RECEIPT", "Title"),
        p("Official Goods Receipt Note (GRN) — Committed to Inventory Ledger"),
        Spacer(1, 16),
        p(f"Record ID: {transaction_id}", "Heading2"),
        p(f"Date: {business_date}"),
        p(f"Merchant / Mill: {merchant_name}"),
        Spacer(1, 14),
    ]
    rows = [[p(x) for x in ("Item / Material", "Quantity Added", "Unit")]]
    for it in items:
        rows.append([
            p(it.get("item_description") or it.get("name") or "Stock Item"),
            p(f"{it.get('quantity') or 0}", "ReceiptRight"),
            p(it.get("unit") or "units"),
        ])
    table = Table(rows, colWidths=[280, 110, 109], repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5edf4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8296aa")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dce3ea")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([table, Spacer(1, 20)])
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=48, rightMargin=48,
                            topMargin=42, bottomMargin=48, title="Stock Intake Receipt", author="Clandexis", invariant=1)
    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#52667a"))
        canvas.drawString(48, 26, "Clandexis | Official Stock Ledger Intake Record")
        canvas.drawRightString(A4[0] - 48, 26, f"Page {document.page}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def generate_transaction_pdf(merchant_id: str, txn_id: str) -> tuple[bytes, str]:
    """Universal PDF document generator for ANY committed transaction (sales or stock intake)."""
    from app.services.persistence import ledger
    if hasattr(ledger, "get_db"):
        with ledger.get_db().transaction(merchant_id) as conn:
            tx = conn.execute(
                "SELECT t.*, p.display_name AS buyer_name, m.business_name AS merchant_name "
                "FROM transactions t "
                "LEFT JOIN parties p ON p.id = t.party_id "
                "LEFT JOIN merchants m ON m.id = t.merchant_id "
                "WHERE t.merchant_id=%s AND t.id=%s",
                (merchant_id, txn_id),
            ).fetchone()
            if not tx:
                raise ValueError(f"Transaction {txn_id} not found in database")

            items = conn.execute(
                "SELECT * FROM transaction_items WHERE transaction_id=%s",
                (txn_id,),
            ).fetchall()
            items_list = [dict(it) for it in items]

            intent = tx.get("intent") or "SALE_INVOICE"
            m_name = tx.get("merchant_name") or "Clandexis Merchant"
            b_date = str(tx.get("business_date") or date.today())

            if intent in ("PRODUCTION_LOG", "STOCK_ADDED", "OPENING_BALANCE"):
                pdf_bytes = render_stock_intake_receipt(
                    merchant_name=m_name,
                    transaction_id=str(txn_id),
                    business_date=b_date,
                    items=items_list,
                )
                return pdf_bytes, f"stock-intake-{str(txn_id)[:8]}.pdf"
            else:
                sale_items = []
                for it in items_list:
                    qty = Decimal(str(it.get("quantity") or 1))
                    price = Decimal(str(it.get("unit_price") or 0))
                    total = Decimal(str(it.get("line_total") or (qty * price)))
                    sale_items.append(InvoiceItem(
                        name=it.get("item_description") or "Item",
                        quantity=qty,
                        unit=it.get("unit") or "pieces",
                        unit_price=price,
                        discount=Decimal("0"),
                        line_total=total,
                    ))
                if not sale_items:
                    tot_amt = Decimal(str(tx.get("total_amount") or 0))
                    sale_items.append(InvoiceItem(
                        name="General Merchandise",
                        quantity=Decimal("1"),
                        unit="order",
                        unit_price=tot_amt,
                        discount=Decimal("0"),
                        line_total=tot_amt,
                    ))
                subtot = sum((i.line_total for i in sale_items), Decimal(0))
                sale = CommittedSale(
                    merchant_id=str(merchant_id),
                    transaction_id=str(txn_id),
                    status="COMMITTED",
                    intent="SALE_INVOICE",
                    merchant_name=m_name,
                    buyer_name=tx.get("buyer_name") or "Customer",
                    invoice_number=str(txn_id)[:8].upper(),
                    invoice_date=date.fromisoformat(b_date) if isinstance(b_date, str) and len(b_date) == 10 else date.today(),
                    currency=tx.get("currency") or "INR",
                    items=tuple(sale_items),
                    subtotal=subtot,
                    discount=Decimal(str(tx.get("discount_amount") or 0)),
                    tax=Decimal(str(tx.get("tax_amount") or 0)),
                    total=Decimal(str(tx.get("total_amount") or subtot)),
                    paid=Decimal(str(tx.get("amount_paid") or 0)),
                    due=Decimal(str(tx.get("credit_amount") or 0)),
                )
                pdf_bytes = render_invoice(sale)
                return pdf_bytes, f"invoice-{str(txn_id)[:8]}.pdf"
    raise ValueError("Database backend not configured for PDF generation")


class InvoiceService:
    def __init__(self, repository: InvoiceRepository, artifacts: ArtifactStore):
        self.repository = repository
        self.artifacts = artifacts

    def generate_document(self, authenticated_merchant: AuthenticatedMerchant,
                          transaction_id: str) -> InvoiceArtifact:
        if not isinstance(authenticated_merchant, AuthenticatedMerchant) or not authenticated_merchant.merchant_id:
            raise ValueError("Authenticated merchant required")
        merchant_id = authenticated_merchant.merchant_id
        sale = self.repository.get_committed_sale(merchant_id, transaction_id)
        # Revalidate even if a custom repository used model_construct or an unsafe cache.
        sale = CommittedSale.model_validate(sale.model_dump())
        if sale.merchant_id != merchant_id or sale.transaction_id != transaction_id:
            raise ValueError("Committed sale unavailable for this merchant")
        content = render_invoice(sale)
        digest = sha256(content).hexdigest()
        association = sha256(f"{merchant_id}\0{transaction_id}".encode()).hexdigest()
        key = f"{association}/{digest}.pdf"
        self.artifacts.put_if_absent(key, content)
        artifact = InvoiceArtifact(merchant_id=merchant_id, transaction_id=transaction_id,
                                   storage_key=key, sha256=digest,
                                   filename=f"invoice-{association[:16]}.pdf")
        # Separate from sale posting: failure here can only require a document retry.
        attached = self.repository.associate_invoice(merchant_id, transaction_id, artifact)
        if attached.merchant_id != merchant_id or attached.transaction_id != transaction_id:
            raise ValueError("Invalid invoice association returned by repository")
        return attached
