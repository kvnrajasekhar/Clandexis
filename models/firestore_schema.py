"""
Firestore Database Schema and Pydantic Entity Models for Clandexis Invisible ERP.
Strict multi-tenant architecture with atomic ledger invariants and split payments.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


# ==============================================================================
# Enums
# ==============================================================================

class TransactionIntent(str, Enum):
    PRODUCTION_LOG = "PRODUCTION_LOG"
    SALE_INVOICE = "SALE_INVOICE"
    PURCHASE_ENTRY = "PURCHASE_ENTRY"
    UDHAAR_COLLECTION = "UDHAAR_COLLECTION"
    ANALYTICS_QUERY = "ANALYTICS_QUERY"
    INVENTORY_SETUP = "INVENTORY_SETUP"
    INVENTORY_ADJUSTMENT = "INVENTORY_ADJUSTMENT"
    EXPENSE = "EXPENSE"
    UNKNOWN = "UNKNOWN"


class TransactionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    EXTRACTED = "EXTRACTED"
    VALIDATING = "VALIDATING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    APPROVED = "APPROVED"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class MovementType(str, Enum):
    PRODUCTION_ADD = "PRODUCTION_ADD"
    SALE_DEDUCT = "SALE_DEDUCT"
    RETURN_ADD = "RETURN_ADD"
    ADJUSTMENT = "ADJUSTMENT"
    DAMAGE_WASTAGE = "DAMAGE_WASTAGE"
    AUDIT_ADJUSTMENT = "AUDIT_ADJUSTMENT"


class PaymentMethod(str, Enum):
    UPI = "UPI"
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    UDHAAR_CREDIT = "UDHAAR_CREDIT"
    ADVANCE_ADJUSTED = "ADVANCE_ADJUSTED"


class PaymentStatus(str, Enum):
    SETTLED = "SETTLED"
    PENDING_COLLECTION = "PENDING_COLLECTION"
    APPLIED = "APPLIED"


# ==============================================================================
# Sub-Documents: Line Items & Payment Splits
# ==============================================================================

class LineItem(BaseModel):
    sku_id: Optional[str] = Field(None, description="Resolved catalog SKU ID")
    item_name: str = Field(..., description="Item name in vernacular/English (e.g. 'Cotton Shirt')")
    quantity: float = Field(..., gt=0, description="Quantity sold/manufactured")
    unit: str = Field(default="pieces", description="Unit of measure (pieces, meters, kg, boxes)")
    unit_price: float = Field(default=0.0, ge=0, description="Price per unit in INR (₹)")
    discount: float = Field(default=0.0, ge=0, description="Item discount in INR (₹)")
    tax_rate: float = Field(default=0.0, ge=0, description="Applicable GST % (0, 5, 12, 18)")
    subtotal: float = Field(..., description="Line subtotal: (quantity * unit_price) - discount")


class PaymentNode(BaseModel):
    method: PaymentMethod = Field(..., description="Payment instrument")
    amount: float = Field(..., ge=0, description="Amount allocated to this instrument in INR (₹)")
    status: PaymentStatus = Field(default=PaymentStatus.SETTLED)
    settlement_reference: Optional[str] = Field(None, description="UPI UTR number or bank ref")
    due_date: Optional[str] = Field(None, description="Credit payment due date (e.g., 'next week')")


# ==============================================================================
# Firestore Collections (Entity Models)
# ==============================================================================

class Merchant(BaseModel):
    """
    Root Tenant Document: `merchants/{merchant_id}`
    Primary Key: E.164 Phone Number (+919876543210)
    """
    merchant_id: str = Field(..., description="E.164 phone number: +919876543210")
    business_name: str = Field(..., description="Trading or company name (e.g. 'Sri Balaji Textiles')")
    owner_name: str = Field(..., description="Proprietor name")
    preferred_lang: str = Field(default="hi", description="Language code: hi (Hindi), te (Telugu), en")
    high_credit_threshold: float = Field(default=5000.0, description="Threshold triggering safety gate (₹)")
    allow_negative_stock: bool = Field(default=True, description="Whether negative inventory balances are permitted with warning")
    whatsapp_phone_number_id: Optional[str] = Field(None, description="Meta Cloud API Phone Number ID")
    whatsapp_verified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")


class Party(BaseModel):
    """
    Counterparty Document: `merchants/{merchant_id}/parties/{party_slug}`
    Primary Key: Slug (e.g. 'ramesh-textiles')
    """
    party_id: str = Field(..., description="Unique party slug (e.g. 'ramesh-textiles')")
    display_name: str = Field(..., description="Full display name (e.g. 'Ramesh Textiles')")
    aliases: List[str] = Field(default_factory=list, description="Fuzzy match aliases: ['Ramesh', 'Ramesh Bhai']")
    phone: Optional[str] = Field(None, description="Party contact phone in E.164")
    current_udhaar_balance: float = Field(default=0.0, description="Outstanding credit balance in INR (₹)")
    credit_limit: float = Field(default=50000.0, description="Maximum credit allowance (₹)")
    last_txn_at: Optional[datetime] = None

    model_config = ConfigDict(extra="ignore")


class InventoryItem(BaseModel):
    """
    Catalog & Stock Document: `merchants/{merchant_id}/inventory/{sku_id}`
    Primary Key: SKU identifier (e.g. 'shirt-cotton-l')
    """
    sku_id: str = Field(..., description="Unique catalog SKU: 'shirt-cotton-l'")
    item_name: str = Field(..., description="Catalog item name: 'Cotton Shirt Large'")
    aliases: List[str] = Field(default_factory=list, description="Vernacular aliases: ['cotton shirt', 'sooti kameez']")
    unit: str = Field(default="pieces", description="Stock unit (pieces, meters, kg)")
    current_stock: float = Field(default=0.0, description="Available on-hand inventory quantity")
    default_unit_price: float = Field(default=0.0, description="Default selling price in INR (₹)")
    reorder_level: float = Field(default=20.0, description="Minimum stock triggering reorder warning")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")


class InventoryMovement(BaseModel):
    """
    Append-Only Inventory Audit Log: `merchants/{merchant_id}/inventory_movements/{movement_id}`
    """
    movement_id: str = Field(..., description="UUID for movement record")
    merchant_id: str
    sku_id: str
    movement_type: MovementType
    quantity: float = Field(..., description="Quantity delta (positive or negative)")
    balance_after: float = Field(..., description="Inventory balance immediately following mutation")
    reference_txn_id: str = Field(..., description="Originating transaction UUID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")


class Transaction(BaseModel):
    """
    Core Financial & Production Transaction: `merchants/{merchant_id}/transactions/{txn_id}`
    Primary Key: UUID ('txn_98234723')
    """
    txn_id: str = Field(..., description="Unique transaction ID")
    merchant_id: str = Field(..., description="Tenant owner phone number")
    intent: TransactionIntent
    source_channel: str = Field(default="meta_whatsapp", description="meta_whatsapp, twilio, web_simulator")
    source_media_type: str = Field(default="voice", description="voice, image, text, interactive")
    
    party_id: Optional[str] = Field(None, description="Resolved counterparty slug")
    party_name: Optional[str] = Field(None, description="Extracted counterparty name")
    
    line_items: List[LineItem] = Field(default_factory=list)
    payments: List[PaymentNode] = Field(default_factory=list)
    total_amount: float = Field(default=0.0, ge=0, description="Total transaction monetary value in INR (₹)")
    
    status: TransactionStatus = Field(default=TransactionStatus.RECEIVED)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    ambiguity_reason: Optional[str] = None
    safety_gate_triggered: bool = Field(default=False)
    
    invoice_pdf_url: Optional[str] = None
    idempotency_key: str = Field(..., description="Unique message or request hash preventing double-processing")
    raw_transcript: Optional[str] = None
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    committed_at: Optional[datetime] = None

    model_config = ConfigDict(extra="ignore")


class DigitalAttestation(BaseModel):
    """
    Lender-Ready Attestation Record: `merchants/{merchant_id}/attestations/{report_id}`
    """
    report_id: str = Field(..., description="Attestation report ID: 'rep_2026_q4'")
    merchant_id: str
    period_start: str = Field(..., description="ISO 8601 Date: YYYY-MM-DD")
    period_end: str = Field(..., description="ISO 8601 Date: YYYY-MM-DD")
    pdf_gcs_uri: str = Field(..., description="Cloud Storage URI of the generated report PDF")
    pdf_sha256_hash: str = Field(..., description="Hex SHA-256 hash of canonical PDF bytes")
    kms_signature_b64: str = Field(..., description="Base64 encoded asymmetric signature from Cloud KMS")
    kms_key_version: str = Field(..., description="Full Cloud KMS key version resource identifier")
    credit_health_summary: Dict[str, Any] = Field(default_factory=dict, description="Metrics & Gemini analysis")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")
