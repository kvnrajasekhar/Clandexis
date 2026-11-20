from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class TransactionType(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_MADE = "payment_made"
    STOCK_ADDED = "stock_added"
    STOCK_ADJUSTMENT = "stock_adjustment"
    QUERY = "query"
    UNKNOWN = "unknown"

class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    UNKNOWN = "unknown"

class LineItem(BaseModel):
    item_name: str = Field(description="Name or description of the product/item")
    quantity: Optional[float] = Field(default=None, description="Number of items or amount")
    unit: Optional[str] = Field(default=None, description="Pieces, kg, bags, boxes, metres, packet, etc.")
    unit_price: Optional[float] = Field(default=None, description="Price per unit")
    unit_price_source: Optional[Literal["extracted", "derived"]] = Field(
        default="extracted",
        description="'extracted' if the user/document stated it directly, 'derived' if calculated from line_total / quantity"
    )
    line_total: Optional[float] = Field(default=None, description="Total amount for this specific line item")

class PaymentRecord(BaseModel):
    method: str = Field(description="Actual payment method used: cash, upi, bank_transfer, cheque, card, unknown (never credit/udhaar)")
    amount: float = Field(description="Actual monetary amount paid/received via this method")

class TransactionCandidate(BaseModel):
    schema_version: str = Field(default="1.1", description="Contract schema version")
    detected_language: str = Field(
        default="unknown",
        description="Detected spoken or written language, e.g., 'Telugu', 'te-Latn', 'Hindi', 'English', 'mixed'"
    )
    transaction_type: TransactionType = Field(
        description="Classified business intent: sale, purchase, payment_received, payment_made, stock_added, stock_adjustment, query, unknown"
    )
    buyer_name: Optional[str] = Field(default=None, description="Customer / party name if mentioned")
    supplier_name: Optional[str] = Field(default=None, description="Supplier / vendor name if purchase or bill")
    invoice_number: Optional[str] = Field(default=None, description="Invoice or bill number if present")
    transaction_date: Optional[str] = Field(default=None, description="Normalized ISO date (YYYY-MM-DD) if determinable")
    due_date: Optional[str] = Field(default=None, description="Normalized ISO due date if explicitly known")
    due_date_text: Optional[str] = Field(default=None, description="Original verbatim due date phrase, e.g., 'next Friday', 'repu'")
    currency: str = Field(default="INR", description="Currency code, e.g. INR, USD")
    items: List[LineItem] = Field(default_factory=list, description="Array of products/services")
    
    # Financial breakdown fields
    subtotal: Optional[float] = Field(default=None, description="Subtotal before taxes and discounts")
    tax_amount: Optional[float] = Field(default=None, description="Tax amount (GST, VAT, etc.)")
    discount_amount: Optional[float] = Field(default=None, description="Discount amount applied")
    total_amount: Optional[float] = Field(default=None, description="Total transaction monetary value")
    
    # Actual payments received (never credit/udhaar)
    payments: List[PaymentRecord] = Field(default_factory=list, description="Actual received payments only. Never include credit/udhaar here.")
    amount_paid: Optional[float] = Field(default=None, description="Total amount actually collected/paid so far. null if unmentioned.")
    cash_amount: Optional[float] = Field(default=None, description="Cash amount collected")
    online_amount: Optional[float] = Field(default=None, description="UPI/Online amount collected")
    credit_amount: Optional[float] = Field(default=None, description="Outstanding credit/receivable. null if unmentioned.")
    payment_status: PaymentStatus = Field(default=PaymentStatus.UNKNOWN, description="unpaid, partial, paid, unknown")
    payment_terms: Optional[str] = Field(default=None, description="Declared payment terms e.g., '50% credit', 'Net 30'")
    
    # Ambiguity & Clarification (Model extraction layer)
    confidence: float = Field(default=0.9, description="Model self-reported diagnostic confidence between 0.0 and 1.0")
    missing_fields: List[str] = Field(default_factory=list, description="List of essential fields missing from input")
    needs_clarification: bool = Field(default=False, description="True if critical information is ambiguous, contradictory, or payment status unmentioned")
    clarification_question: Optional[str] = Field(
        default=None,
        description="Clarification question phrased in the merchant's language if needs_clarification is True"
    )
    raw_transcript: Optional[str] = Field(default=None, description="Verbatim transcription if input is audio/voice or source text")
    
    # Backend Stage 4 Business Validation layer fields
    validation_status: Literal["ACCEPTED", "REJECTED", "NEEDS_REVIEW"] = Field(
        default="NEEDS_REVIEW",
        description="Stage 4 validation verdict: ACCEPTED (safe candidate), REJECTED (mathematically invalid/impossible), or NEEDS_REVIEW"
    )
    validation_warnings: List[str] = Field(default_factory=list, description="Warnings identified during mathematical or source reconciliation")
    requires_review: bool = Field(default=False, description="True if transaction has discrepancies or needs human check before committing")

class ExtractionResponse(BaseModel):
    transactions: List[TransactionCandidate] = Field(
        default_factory=list,
        description="List of extracted transaction candidates"
    )

class ProcessedInputPayload(BaseModel):
    merchant_id: str
    message_id: Optional[str] = None
    idempotency_key: str
    is_duplicate: bool = False
    media_type: str = "text"
    media_path: Optional[str] = None
    raw_text: Optional[str] = None

class ProcessingResult(BaseModel):
    input_metadata: ProcessedInputPayload
    extraction: ExtractionResponse
