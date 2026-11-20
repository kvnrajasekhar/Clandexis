"""
Agent Orchestrator Service for Clandexis Invisible ERP.
Handles InboundMessage processing, conversational vernacular NLP / AI extraction,
deterministic invariant validation (validate_split_math), human-in-the-loop safety gating,
atomic ledger mutations (inventory stock & udhaar balances), and outbound message construction.
Also provides orchestrate_inbound_message for multimodal Gemini pipeline integration and staging.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from inventory.inventory_service import InventoryService
from inventory.inventory_analytics import InventoryAnalytics
from parties import MerchantService, PartyService
from models.canonical import CanonicalButton, ChannelType, InboundMessage, MessageType, OutboundMessage
from models.firestore_schema import (
    InventoryItem,
    InventoryMovement,
    LineItem,
    Merchant,
    MovementType,
    Party,
    PaymentMethod,
    PaymentNode,
    PaymentStatus,
    Transaction,
    TransactionIntent,
    TransactionStatus,
)
from .validate_split_math import validate_split_math
from app.schemas.transaction import ProcessingResult
from app.services.confirmation import (
    AuthenticatedMerchant, ConfirmationError, PostingResult, button_id,
    get_confirmation_service,
)

logger = logging.getLogger("agent-orchestrator")
pipeline = None  # Lazy: confirmation callbacks must never initialize the extractor.

INTENT_MAPPING = {
    "sale": TransactionIntent.SALE_INVOICE,
    "purchase": TransactionIntent.PURCHASE_ENTRY,
    "payment_received": TransactionIntent.UDHAAR_COLLECTION,
    "payment_made": TransactionIntent.EXPENSE,
    "stock_added": TransactionIntent.PRODUCTION_LOG,
    "stock_adjustment": TransactionIntent.PRODUCTION_LOG,
    "query": TransactionIntent.ANALYTICS_QUERY,
    "unknown": TransactionIntent.UNKNOWN,
}


def posting_outbound(phone: str, result: PostingResult) -> OutboundMessage:
    balances = ", ".join(f"{sku}: {quantity}" for sku, quantity in result.inventory_balance.items())
    return OutboundMessage(
        recipient_phone=phone,
        text_content=(
            f"Posting status: {result.posting_status}\nTransaction: {result.transaction_id}\n"
            f"Amount paid: {result.amount_paid}\nCredit outstanding: {result.credit_outstanding}\n"
            f"Inventory balance: {balances or 'No inventory movement'}"
        ),
    )


class AgentOrchestrator:
    """
    Central business engine and conversational agent orchestrator for Clandexis.
    Provides multi-tenant ledger management, intent routing, entity extraction,
    safety gating, and automated WhatsApp outbound response generation.
    """

    def __init__(self):
        self.merchants: Dict[str, Merchant] = {}
        self.parties: Dict[str, Dict[str, Party]] = {}  # merchant_id -> {party_id: Party}
        self.inventory: Dict[str, Dict[str, InventoryItem]] = {}  # merchant_id -> {sku_id: InventoryItem}
        self.transactions: Dict[str, Transaction] = {}  # txn_id -> Transaction
        self.movements: Dict[str, List[InventoryMovement]] = {}  # merchant_id -> [InventoryMovement]

        self.inventory_service = InventoryService(
            inventory_store=self.inventory,
            movements_store=self.movements,
            merchants_store=self.merchants,
        )
        self.inventory_analytics = InventoryAnalytics(
            inventory_store=self.inventory,
            movements_store=self.movements,
            transactions_store=self.transactions,
        )
        self.party_service = PartyService(
            parties_store=self.parties,
            merchants_store=self.merchants,
            transactions_store=self.transactions,
        )
        self.merchant_service = MerchantService(
            merchants_store=self.merchants,
            parties_store=self.parties,
            inventory_store=self.inventory,
        )

        self._seed_default_tenant()

    def _seed_default_tenant(self):
        """Populates baseline MSME demo data (Sri Balaji Textiles)."""
        merchant_id = "+919876543210"
        self.merchants[merchant_id] = Merchant(
            merchant_id=merchant_id,
            business_name="Sri Balaji Textiles",
            owner_name="Rajesh Kumar",
            preferred_lang="hi",
            high_credit_threshold=settings.HIGH_CREDIT_THRESHOLD,
            whatsapp_phone_number_id=settings.META_PHONE_NUMBER_ID or "15550269999",
            whatsapp_verified_at=datetime.now(timezone.utc),
        )

        self.parties[merchant_id] = {
            "ramesh-textiles": Party(
                party_id="ramesh-textiles",
                display_name="Ramesh Textiles",
                aliases=["ramesh", "ramesh bhai", "ramesh garments"],
                phone="+919811122233",
                current_udhaar_balance=2500.0,
                credit_limit=50000.0,
                last_txn_at=datetime.now(timezone.utc),
            ),
            "suresh-traders": Party(
                party_id="suresh-traders",
                display_name="Suresh Traders",
                aliases=["suresh", "suresh ji", "suresh wholesale"],
                phone="+919844455566",
                current_udhaar_balance=0.0,
                credit_limit=30000.0,
                last_txn_at=datetime.now(timezone.utc),
            ),
        }

        self.inventory[merchant_id] = {
            "shirt-cotton-l": InventoryItem(
                sku_id="shirt-cotton-l",
                item_name="Cotton Shirt (L)",
                aliases=["cotton shirt", "cotton shirts", "shirt", "sooti shirt"],
                unit="pieces",
                current_stock=300.0,
                default_unit_price=200.0,
                reorder_level=50.0,
                updated_at=datetime.now(timezone.utc),
            ),
            "kurta-linen-m": InventoryItem(
                sku_id="kurta-linen-m",
                item_name="Linen Kurta (M)",
                aliases=["linen kurta", "kurta", "khadi kurta"],
                unit="pieces",
                current_stock=150.0,
                default_unit_price=450.0,
                reorder_level=25.0,
                updated_at=datetime.now(timezone.utc),
            ),
        }
        self.movements[merchant_id] = []

    def _init_tenant(self, tenant_id: str, sender_name: Optional[str] = None):
        """Initializes a new merchant tenant with standard defaults."""
        self.merchant_service.setup_or_update_merchant(
            merchant_id=tenant_id,
            business_name=f"{sender_name or 'MSME'} Enterprises",
            owner_name=sender_name or "Merchant Owner",
            preferred_lang="hi",
            high_credit_threshold=settings.HIGH_CREDIT_THRESHOLD,
            whatsapp_phone_number_id=settings.META_PHONE_NUMBER_ID,
        )
        if tenant_id not in self.movements:
            self.movements[tenant_id] = []

    async def handle_inbound_message(
        self,
        message: InboundMessage,
        media_bytes: Optional[bytes] = None,
    ) -> List[OutboundMessage]:
        """
        Processes an incoming InboundMessage from WhatsApp and returns one or more OutboundMessages.
        """
        sender_phone = message.sender_phone
        tenant_id = message.tenant_id

        if tenant_id not in self.merchants:
            self._init_tenant(tenant_id, message.sender_name)

        # Case 1: 1-Tap Interactive Quick-Reply Button Click
        if message.message_type == MessageType.INTERACTIVE_BUTTON or message.interactive_id:
            outbound = self._handle_interactive_button(message)
            return [outbound]

        # Case 2: Text / Vernacular Voice Note / Bill Image
        text = message.text_content or ""

        # Check if text is a query (balance, stock, help)
        query_response = self._handle_query(text, tenant_id, sender_phone)
        if query_response:
            return [query_response]

        # Otherwise, process as a business transaction (Sale, Purchase, Production)
        return [await self._process_transaction_input(message, text, media_bytes)]

    def _handle_interactive_button(self, message: InboundMessage) -> OutboundMessage:
        """
        Handles interactive button callbacks:
        - CONFIRM_TXN_{id} / CONFIRM_NEW_PARTY_TXN_{id} / clx:v1:confirm:{id}
        - CANCEL_TXN_{id} / clx:v1:cancel:{id}
        """
        action_id = message.interactive_id or ""
        tenant_id = message.tenant_id
        recipient_phone = message.sender_phone

        is_new_party_confirm = action_id.startswith("CONFIRM_NEW_PARTY_TXN_")
        is_confirm = action_id.startswith("CONFIRM_TXN_") or is_new_party_confirm or action_id.startswith("clx:v1:confirm:")

        if is_confirm:
            if is_new_party_confirm:
                txn_id = action_id[len("CONFIRM_NEW_PARTY_TXN_"):]
            elif action_id.startswith("CONFIRM_TXN_"):
                txn_id = action_id[len("CONFIRM_TXN_"):]
            else:
                txn_id = action_id[len("clx:v1:confirm:"):]

            txn = self.transactions.get(txn_id)
            if not txn:
                return OutboundMessage(
                    recipient_phone=recipient_phone,
                    channel=ChannelType.META_WHATSAPP,
                    message_type=MessageType.TEXT,
                    text_content="⚠️ *Transaction Not Found / समाप्त*\nThis transaction was already processed or does not exist.",
                )

            if txn.status == TransactionStatus.COMMITTED:
                return OutboundMessage(
                    recipient_phone=recipient_phone,
                    channel=ChannelType.META_WHATSAPP,
                    message_type=MessageType.TEXT,
                    text_content=f"ℹ️ Transaction `{txn_id}` is already confirmed and committed.",
                )

            # Auto-onboard party if unknown or missing from party master
            new_party_msg = ""
            if txn.party_name and txn.party_name not in ("Counterparty", "Internal Workshop", "Internal Stock Adjustment", "Catalog Administration"):
                if not txn.party_id or txn.party_id not in self.parties.get(tenant_id, {}):
                    party, _ = self.party_service.create_or_update_party(
                        merchant_id=tenant_id,
                        display_name=txn.party_name,
                    )
                    txn.party_id = party.party_id
                    txn.party_name = party.display_name
                    new_party_msg = f"\n👤 *New Customer Onboarded:* {party.display_name} (`{party.party_id}`)"

            # Atomically commit ledger mutation
            self._commit_ledger_transaction(txn)
            txn.status = TransactionStatus.COMMITTED
            txn.committed_at = datetime.now(timezone.utc)

            # Format itemized success summary
            party_info = txn.party_name or "Counterparty"
            udhaar_amount = sum(p.amount for p in txn.payments if p.method == PaymentMethod.UDHAAR_CREDIT)

            party_balance_str = ""
            if txn.party_id and txn.party_id in self.parties.get(tenant_id, {}):
                party = self.parties[tenant_id][txn.party_id]
                party_balance_str = f"\n📒 *Total Outstanding Udhaar:* ₹{party.current_udhaar_balance:,.2f}"

            body = (
                f"✅ *लेन-देन स्वीकृत और दर्ज (Transaction Committed)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 *Txn ID:* `{txn.txn_id}`\n"
                f"👤 *Party:* {party_info}\n"
                f"💰 *Total Amount:* ₹{txn.total_amount:,.2f}\n"
                f"💵 *Settled:* ₹{(txn.total_amount - udhaar_amount):,.2f}\n"
                f"⏳ *Udhaar Added:* ₹{udhaar_amount:,.2f}"
                f"{party_balance_str}"
                f"{new_party_msg}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Ledger & stock balances updated atomically. Digital receipt generated."
            )

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=body,
            )

        elif action_id.startswith("CANCEL_TXN_") or action_id.startswith("clx:v1:cancel:"):
            prefix = "CANCEL_TXN_" if action_id.startswith("CANCEL_TXN_") else "clx:v1:cancel:"
            txn_id = action_id[len(prefix):]
            txn = self.transactions.get(txn_id)
            if txn:
                txn.status = TransactionStatus.REJECTED

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=(
                    f"❌ *लेन-देन रद्द किया गया (Transaction Cancelled)*\n\n"
                    f"Transaction `{txn_id}` was rejected. No changes were made to your stock or ledger."
                ),
            )

        return OutboundMessage(
            recipient_phone=recipient_phone,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content=f"Received button selection: {message.interactive_title or action_id}",
        )

    def _handle_query(self, text: str, tenant_id: str, recipient_phone: str) -> Optional[OutboundMessage]:
        """
        Detects read-only queries (stock level, udhaar balance, summary, help).
        Only triggers if transaction recording action words are absent.
        """
        t = text.lower().strip()
        if not t:
            return None

        # Action commands must NOT be intercepted as read queries
        action_prefixes = (
            "add item", "add sku", "naya item", "naya maal", "item add", "kotha saraku", "nayi item",
            "bechi", "becha", "sold", "diya", "kharida", "purchase",
            "banayi", "banaya", "manufactured", "likh lo", "likho", "record",
            "kharab", "damage", "wastage",
            "add customer", "naya customer", "naya grahak", "customer add", "grahak add", "kotha customer", "khata kholo",
            "setup business", "register business", "business setup", "mera business setup",
            "update upi", "change upi", "update address", "address update", "set language", "credit threshold", "credit limit threshold"
        )
        if any(w in t for w in action_prefixes):
            return None

        # 1. Help / Info Query & Greetings
        if t in ("help", "madad", "menu", "hi", "hello", "namaste", "shuru", "start"):
            help_text = (
                "🙏 *नमस्ते! Welcome to Clandexis (Invisible ERP)*\n"
                "Aap seedhe vernacular WhatsApp voice ya text me likhein:\n\n"
                "📌 *MSME Setup & Profile:*\n"
                "• _\"Setup business Sri Balaji Textiles, owner Rajesh, UPI balaji@upi\"_\n"
                "• _\"Mera business profile dikhao\"_\n"
                "• _\"Update UPI to rajesh@icici\"_\n\n"
                "📌 *Customer Onboarding & Khata:*\n"
                "• _\"Naya customer add karo: Gupta Stores, phone 9811223344, credit limit 20000\"_\n"
                "• _\"Customer list dikhao\"_\n"
                "• _\"Gupta Stores ka khata dikhao\"_\n\n"
                "📌 *Inventory Setup:*\n"
                "• _\"Naya item add karo Silk Saree 50 pieces @ 1200 reorder 10\"_\n\n"
                "📌 *Daily Production:*\n"
                "• _\"Aaj 200 cotton shirts banayi batch B14\"_\n\n"
                "📌 *Stock Damage / Wastage:*\n"
                "• _\"10 cotton shirts kharab ho gayi likh lo\"_\n\n"
                "📌 *Record Sale/Udhaar:*\n"
                "• _\"Ramesh ko 10 cotton shirt bechi ₹2000 ki, ₹500 cash diya baaki udhaar\"_\n\n"
                "📌 *Stock & Production Analytics:*\n"
                "• _\"Stock kitna hai?\"_\n"
                "• _\"Kaun sa maal khatam hone wala hai?\"_ (Low stock alert)\n"
                "• _\"Aaj kitna maal bana aur kitna bika?\"_ (Production vs Sales)\n"
                "• _\"Aaj kitna maal bana?\"_ (Production summary)\n\n"
                "📌 *Daily Summary:*\n"
                "• _\"Aaj ki bikri ka summary do\"_"
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=help_text,
            )

        # 2. MSME Business Profile Query
        if any(w in t for w in ("business profile", "mera profile", "profile dikhao", "account profile", "account details", "account status", "my business", "profile check", "check my account", "meri dukaan", "meri dukan")):
            prof = self.merchant_service.get_merchant_profile(tenant_id)
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=prof["formatted_text"],
            )

        # 3. Customer / Party List Query
        if any(w in t for w in ("customer list", "customers list", "all customers", "sabhi grahak", "grahak soochi", "grahak suchi", "parties list", "parties", "counterparties")):
            plist = self.party_service.list_parties(tenant_id)
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=plist["formatted_text"],
            )

        # 4. Production vs. Sales Comparison Query
        if ("bana" in t and ("bika" in t or "becha" in t or "bikri" in t)) or "production vs sales" in t:
            report = self.inventory_analytics.get_production_vs_sales(tenant_id, period="today")
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=report["formatted_text"],
            )

        # 5. Production Output / Summary Query
        if ("kitna" in t or "aaj" in t or "kya" in t) and any(w in t for w in ("maal bana", "production", "output", "thayaru")) and not any(w in t for w in ("banayi", "banaya")):
            summary = self.inventory_analytics.get_production_summary(tenant_id, period="today")
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=summary["formatted_text"],
            )

        # 6. Low Stock / Reorder Alert Query
        if any(w in t for w in ("low stock", "khatam", "reorder", "alert", "kam hai")):
            alerts = self.inventory_analytics.get_low_stock_alerts(tenant_id)
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=alerts["formatted_text"],
            )

        # 7. Item Movement History Query
        if any(w in t for w in ("history", "movement", "audit")) and any(w in t for w in ("stock", "maal", "shirt", "kurta", "item")):
            resolved_sku, _, _ = self.inventory_service.resolve_sku(tenant_id, t)
            target_sku = resolved_sku or "shirt-cotton-l"
            history = self.inventory_analytics.get_movement_history(tenant_id, target_sku)
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=history["formatted_text"],
            )

        # 8. Stock Query (Item-Specific or All Catalog)
        if any(w in t for w in ("stock", "maal", "inventory", "saraku")) or ("kitna" in t and any(w in t for w in ("shirt", "kurta", "bacha", "bache", "migilindi"))):
            resolved_sku, _, conf = self.inventory_service.resolve_sku(tenant_id, t)
            sku_filter = resolved_sku if conf >= 0.85 else None
            status_data = self.inventory_analytics.get_stock_status(tenant_id, sku_id=sku_filter)
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=status_data["formatted_text"],
            )

        # 9. Party Udhaar / Customer Statement Query
        is_query_phrase = any(q in t for q in ("kitna", "kitne", "batao", "dikhao", "check", "kya", "list", "status", "statement")) or t in ("udhaar", "hisaab", "hisab", "khata", "balance")
        if is_query_phrase and any(w in t for w in ("udhaar", "hisaab", "hisab", "khata", "balance", "baaki", "statement")):
            matched_party, conf = self.party_service.resolve_party(tenant_id, t)
            if matched_party:
                stmt = self.party_service.get_party_statement(tenant_id, matched_party.party_id)
                msg = stmt["formatted_text"]
            else:
                plist = self.party_service.list_parties(tenant_id)
                msg = plist["formatted_text"]

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        # 10. Summary / Sales Report
        if any(w in t for w in ("summary", "bikri report", "sale report")) or t in ("bikri", "report"):
            txns = [txn for txn in self.transactions.values() if txn.merchant_id == tenant_id]
            total_sales = sum(t.total_amount for t in txns if t.status == TransactionStatus.COMMITTED)
            msg = (
                f"📊 *MSME Business Snapshot (व्यापार सारांश)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Committed Sales:* ₹{total_sales:,.2f}\n"
                f"🧾 *Total Transactions:* {len(txns)}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Reply with 'udhaar' or 'stock' for detailed breakdowns."
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        return None

    async def _process_transaction_input(
        self,
        message: InboundMessage,
        text: str,
        media_bytes: Optional[bytes] = None,
    ) -> OutboundMessage:
        """
        Parses transaction details from text/voice/bill, validates invariants,
        and either stages with 1-tap confirmation buttons or auto-commits.
        """
        tenant_id = message.tenant_id
        recipient_phone = message.sender_phone
        t = text.lower().strip()

        # 1. Vernacular Entity Extraction
        intent, party_name, line_items, payments, total_amount, confidence_score = (
            self._extract_entities_from_text(text, tenant_id)
        )

        # Handle Immediate Onboarding Intents
        if intent == TransactionIntent.CUSTOMER_ONBOARDING:
            phone_m = re.search(r"(\+?91\d{10}|\b\d{10}\b)", text)
            phone = phone_m.group(1) if phone_m else None

            limit_m = re.search(r"(?:limit|seema|sima)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
            credit_limit = float(limit_m.group(1)) if limit_m else 50000.0

            bal_m = re.search(r"(?:opening|initial|purana udhaar|balance)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
            opening_balance = float(bal_m.group(1)) if bal_m else 0.0

            clean_name = text
            for phrase in ("naya customer add karo", "naya customer", "add customer", "naya grahak add karo", "naya grahak", "customer add karo", "customer add", "grahak add", "kotha customer", "khata kholo"):
                clean_name = re.sub(phrase, "", clean_name, flags=re.IGNORECASE)
            if phone:
                clean_name = clean_name.replace(phone, "")
            clean_name = re.sub(r"(?:phone|mobile|ph|contact|no|number)\b", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r"(?:credit limit|limit|seema|sima)\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r"(?:opening|initial|purana udhaar|opening balance|balance)\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r"(?:udhaar|credit)\b", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r"[,:\-_]", " ", clean_name)
            clean_name = re.sub(r"\s+", " ", clean_name).strip()
            display_name = clean_name.title() if clean_name else "New Customer"

            party, msg = self.party_service.create_or_update_party(
                merchant_id=tenant_id,
                display_name=display_name,
                phone=phone,
                credit_limit=credit_limit,
                opening_udhaar_balance=opening_balance,
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        if intent == TransactionIntent.MERCHANT_SETUP:
            upi_match = re.search(r"([a-zA-Z0-9.\-_]+@[a-zA-Z0-9]+)", text)
            upi_vpa = upi_match.group(1) if upi_match else None

            gstin_match = re.search(r"\b([0-9]{2}[a-zA-Z]{5}[0-9]{4}[a-zA-Z]{1}[1-9a-zA-Z]{1}z[0-9a-zA-Z]{1})\b", text, re.IGNORECASE)
            gstin = gstin_match.group(1).upper() if gstin_match else None

            owner_match = re.search(r"(?:owner|proprietor|malik)\s*(?:is|name|:)?\s*([a-zA-Z\s]+?)(?:,|$|upi|gstin|address|phone)", text, re.IGNORECASE)
            owner_name = owner_match.group(1).strip().title() if owner_match else "Merchant Owner"

            bus_match = re.search(r"(?:business|dukaan|dukan|firm|company|store)\s*(?:name|is|:)?\s*([a-zA-Z0-9\s]+?)(?:,|$|owner|proprietor|malik|upi|gstin|address)", text, re.IGNORECASE)
            if bus_match:
                business_name = bus_match.group(1).strip().title()
            else:
                clean_name = text
                for phrase in ("setup business", "register business", "mera business", "business setup"):
                    clean_name = re.sub(phrase, "", clean_name, flags=re.IGNORECASE)
                if upi_vpa:
                    clean_name = clean_name.replace(upi_vpa, "")
                if gstin:
                    clean_name = clean_name.replace(gstin, "")
                clean_name = re.sub(r"(?:owner|proprietor|malik)\s*[a-zA-Z\s]+", "", clean_name, flags=re.IGNORECASE)
                clean_name = re.sub(r"[,:\-_]", " ", clean_name).strip()
                business_name = clean_name.title() if clean_name else "MSME Enterprise"

            merchant, msg = self.merchant_service.setup_or_update_merchant(
                merchant_id=tenant_id,
                business_name=business_name,
                owner_name=owner_name,
                gstin=gstin,
                upi_vpa=upi_vpa,
                preferred_lang="hi",
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        if intent == TransactionIntent.MERCHANT_UPDATE:
            field = "business_name"
            val = ""
            if "upi" in t:
                field = "upi_vpa"
                upi_m = re.search(r"([a-zA-Z0-9.\-_]+@[a-zA-Z0-9]+)", text)
                val = upi_m.group(1) if upi_m else "merchant@upi"
            elif any(w in t for w in ("address", "pata", "premises")):
                field = "business_address"
                val = re.sub(r".*?(?:address|pata)\s*(?:to|is|karke|:)?\s*", "", text, flags=re.IGNORECASE).strip().title()
            elif any(w in t for w in ("language", "bhasha")):
                field = "preferred_lang"
                val = "te" if "telugu" in t else ("en" if "english" in t else "hi")
            elif any(w in t for w in ("credit threshold", "safety limit", "threshold")):
                field = "high_credit_threshold"
                amt_m = re.search(r"(\d+(?:\.\d+)?)", text)
                val = float(amt_m.group(1)) if amt_m else 5000.0

            merchant, msg = self.merchant_service.update_merchant_field(
                merchant_id=tenant_id,
                field_name=field,
                value=val,
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        # Resolve party ID if exists
        party_id = None
        if party_name:
            for pid, party in self.parties.get(tenant_id, {}).items():
                if party.display_name.lower() == party_name.lower() or any(
                    a in party_name.lower() for a in party.aliases
                ):
                    party_id = pid
                    party_name = party.display_name
                    break

        # Check unknown party safety trigger
        unknown_party_flag = False
        if party_name and not party_id and party_name not in ("Counterparty", "Internal Workshop", "Internal Stock Adjustment", "Catalog Administration"):
            unknown_party_flag = True
            confidence_score = min(confidence_score, 0.70)

        # 2. Invariant Math Validation & Safety Gate Check
        is_math_valid, gate_reason, requires_safety_gate = validate_split_math(
            total_amount=total_amount,
            line_items=line_items,
            payments=payments,
            confidence_score=confidence_score,
        )

        if unknown_party_flag:
            requires_safety_gate = True
            gate_reason = f"New Customer '{party_name}' not in Khata. Confirm to onboard customer and commit transaction."

        if not is_math_valid:
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=(
                    f"❌ *Financial Invariant Validation Failed (गणना त्रुटि)*\n\n"
                    f"{gate_reason}\n\n"
                    f"Please re-send with matching items and payments."
                ),
            )

        # 3. Create Transaction Record
        txn_id = f"txn_{uuid.uuid4().hex[:8]}"
        txn = Transaction(
            txn_id=txn_id,
            merchant_id=tenant_id,
            intent=intent,
            source_channel=message.channel.value if hasattr(message.channel, "value") else str(message.channel),
            source_media_type=message.message_type.value if hasattr(message.message_type, "value") else str(message.message_type),
            party_id=party_id,
            party_name=party_name or "Counterparty",
            line_items=line_items,
            payments=payments,
            total_amount=total_amount,
            status=TransactionStatus.AWAITING_CONFIRMATION if requires_safety_gate else TransactionStatus.COMMITTED,
            confidence_score=confidence_score,
            ambiguity_reason=gate_reason,
            safety_gate_triggered=requires_safety_gate,
            idempotency_key=message.message_id,
            raw_transcript=text,
            committed_at=datetime.now(timezone.utc) if not requires_safety_gate else None,
        )
        self.transactions[txn_id] = txn

        # 4. Human-in-the-Loop Safety Gate Branch
        if requires_safety_gate:
            udhaar_val = sum(p.amount for p in payments if p.method == PaymentMethod.UDHAAR_CREDIT)
            cash_val = sum(p.amount for p in payments if p.method != PaymentMethod.UDHAAR_CREDIT)
            items_desc = ", ".join([f"{item.quantity:.0f} {item.item_name}" for item in line_items]) or "Items"

            gate_body = (
                f"*Confirmation Required (पुष्टि करें):*\n\n"
                f"👤 *Party:* {txn.party_name}\n"
                f"📦 *Goods:* {items_desc}\n"
                f"💰 *Total:* ₹{total_amount:,.2f}\n"
                f"💵 *Paid:* ₹{cash_val:,.2f}\n"
                f"📒 *Udhaar:* ₹{udhaar_val:,.2f}\n\n"
                f"⚠️ *Notice:* {gate_reason}\n\n"
                f"Do you want to confirm this transaction?"
            )

            confirm_btn_id = f"CONFIRM_NEW_PARTY_TXN_{txn_id}" if unknown_party_flag else f"CONFIRM_TXN_{txn_id}"
            buttons = [
                CanonicalButton(id=confirm_btn_id, title="✅ Confirm (हाँ)"),
                CanonicalButton(id=f"CANCEL_TXN_{txn_id}", title="❌ Cancel (रद्द)"),
            ]

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.INTERACTIVE_BUTTON,
                interactive_header="⚠️ Safety Gate Verification",
                interactive_body=gate_body,
                interactive_footer="Clandexis Invisible ERP",
                buttons=buttons,
            )

        # 5. Auto-Commit Branch
        self._commit_ledger_transaction(txn)
        udhaar_val = sum(p.amount for p in payments if p.method == PaymentMethod.UDHAAR_CREDIT)
        settled_val = total_amount - udhaar_val

        if txn.intent == TransactionIntent.INVENTORY_SETUP:
            items_desc = "\n".join([f"• *{it.item_name}:* Initial Stock {it.quantity:.0f} {it.unit} @ ₹{it.unit_price:,.2f}" for it in line_items])
            success_text = (
                f"✅ *Transaction Recorded (लेन-देन दर्ज)*\n"
                f"📦 *Product Catalog Updated (उत्पाद सूची अपडेट)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 *Txn ID:* `{txn_id}`\n"
                f"{items_desc}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"New SKU registered and stock balance initialized."
            )
        elif txn.intent == TransactionIntent.INVENTORY_ADJUSTMENT:
            items_desc = "\n".join([f"• *{it.item_name}:* -{it.quantity:.0f} {it.unit} (Damage/Wastage)" for it in line_items])
            inv_map = self.inventory.get(tenant_id, {})
            stock_info = ", ".join([f"{inv_map[it.sku_id].current_stock:.0f} {it.unit}" for it in line_items if it.sku_id in inv_map])
            success_text = (
                f"✅ *Transaction Recorded (लेन-देन दर्ज)*\n"
                f"⚠️ *Stock Adjustment Recorded (स्टॉक समायोजन)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 *Txn ID:* `{txn_id}`\n"
                f"{items_desc}\n"
                f"📊 *Remaining Stock:* {stock_info or 'Updated'}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Inventory updated with damage/adjustment movement."
            )
        elif txn.intent == TransactionIntent.PRODUCTION_LOG:
            items_desc = "\n".join([f"• *{it.item_name}:* +{it.quantity:.0f} {it.unit}" for it in line_items])
            inv_map = self.inventory.get(tenant_id, {})
            stock_info = ", ".join([f"{inv_map[it.sku_id].current_stock:.0f} {it.unit}" for it in line_items if it.sku_id in inv_map])
            success_text = (
                f"✅ *Transaction Recorded (लेन-देन दर्ज)*\n"
                f"🏭 *Daily Production Logged (दैनिक उत्पादन दर्ज)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 *Txn ID:* `{txn_id}`\n"
                f"{items_desc}\n"
                f"📊 *Current Stock Balance:* {stock_info or 'Updated'}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Production movement recorded with audit trail."
            )
        else:
            items_desc = "\n".join([f"• {it.quantity:.0f}x {it.item_name} @ ₹{it.unit_price:.2f} = ₹{it.subtotal:.2f}" for it in line_items])
            success_text = (
                f"✅ *Transaction Recorded (लेन-देन दर्ज)*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 *Txn ID:* `{txn_id}`\n"
                f"👤 *Party:* {txn.party_name}\n"
                f"{items_desc}\n\n"
                f"💰 *Total:* ₹{total_amount:,.2f}\n"
                f"💵 *Settled:* ₹{settled_val:,.2f}\n"
                f"📒 *Udhaar Added:* ₹{udhaar_val:,.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Digital ledger updated."
            )

        return OutboundMessage(
            recipient_phone=recipient_phone,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content=success_text,
        )

    def _extract_entities_from_text(
        self,
        text: str,
        tenant_id: str,
    ) -> Tuple[TransactionIntent, Optional[str], List[LineItem], List[PaymentNode], float, float]:
        """
        Extracts financial intent, party, line items, and split payments.
        Deterministic parser with zero hardcoded catalog fallbacks.
        """
        t = text.lower()
        confidence = 0.95

        # 0a. Check Customer Onboarding Intent
        if any(w in t for w in ("add customer", "naya customer", "naya grahak", "customer add", "grahak add", "kotha customer", "khata kholo")):
            return TransactionIntent.CUSTOMER_ONBOARDING, None, [], [], 0.0, 1.0

        # 0b. Check Merchant Setup Intent
        if any(w in t for w in ("setup business", "register business", "mera business", "business setup")):
            return TransactionIntent.MERCHANT_SETUP, None, [], [], 0.0, 1.0

        # 0c. Check Merchant Update Intent
        if any(w in t for w in ("update upi", "change upi", "update address", "address update", "set language", "credit threshold", "credit limit threshold")):
            return TransactionIntent.MERCHANT_UPDATE, None, [], [], 0.0, 1.0

        # Identify party using party_service fuzzy matcher
        matched_party, party_conf = self.party_service.resolve_party(tenant_id, t)
        party_name = matched_party.display_name if matched_party else None

        if not party_name and any(w in t for w in ("ko", "to")):
            cand = None
            if " to " in t or t.startswith("to "):
                to_m = re.search(r"\bto\s+([a-zA-Z\s]+?)(?:\s+for|\s+rs|\s+₹|\s*\d|\s+ka|\s+ki|$)", t)
                if to_m:
                    cand = to_m.group(1).strip()
            elif " ko " in t or t.startswith("ko "):
                ko_m = re.search(r"(?:^|sold\s+|becha\s+|bechi\s+)?([a-zA-Z\s]+?)\s+ko\b", t)
                if ko_m:
                    cand = ko_m.group(1).strip()

            if cand:
                filter_words = {"aaj", "kal", "batch", "workshop", "him", "her", "them", "cash", "online", "pieces", "meters", "kg", "boxes", "cotton", "shirt", "kurta"}
                cand_clean = " ".join([w for w in cand.split() if w not in filter_words])
                if cand_clean and len(cand_clean) > 2:
                    party_name = cand_clean.title()
                    confidence = 0.70

        # 1. Check Inventory Catalog Setup Intent
        if any(w in t for w in ("add item", "add sku", "naya item", "naya maal", "item add", "kotha saraku", "nayi item")):
            intent = TransactionIntent.INVENTORY_SETUP
            qty_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:pieces|pcs|meters|m|kg|boxes|units)?", t)
            quantity = float(qty_match.group(1)) if qty_match else 0.0

            price_match = re.search(r"(?:@|rate|price|rs\.?|₹)\s*(\d+(?:\.\d+)?)", t)
            unit_price = float(price_match.group(1)) if price_match else 200.0

            reorder_match = re.search(r"(?:reorder|alert|minimum)\s*(?:level|at)?\s*(\d+(?:\.\d+)?)", t)
            reorder_level = float(reorder_match.group(1)) if reorder_match else 20.0

            clean_name = t
            for phrase in ("naya item add karo", "naya item add", "add item", "add sku", "naya maal add karo", "naya maal add", "naya maal", "item add", "kotha saraku", "nayi item"):
                clean_name = clean_name.replace(phrase, "")
            clean_name = re.sub(r"(?:@|rate|price|rs\.?|₹)\s*\d+(?:\.\d+)?", "", clean_name)
            clean_name = re.sub(r"(?:reorder|alert|minimum)\s*(?:level|at)?\s*\d+(?:\.\d+)?", "", clean_name)
            clean_name = re.sub(r"\d+(?:\.\d+)?\s*(?:pieces|pcs|meters|m|kg|boxes|units)?", "", clean_name)
            clean_name = re.sub(r"[^a-zA-Z\s]", "", clean_name).strip()
            item_name = clean_name.title() if clean_name else "New Product"

            clean_slug = re.sub(r"[^a-zA-Z0-9]+", "-", item_name.lower()).strip("-")
            sku_id = clean_slug or f"sku-{uuid.uuid4().hex[:6]}"

            line_items = [
                LineItem(
                    sku_id=sku_id,
                    item_name=item_name,
                    quantity=quantity,
                    unit="pieces",
                    unit_price=unit_price,
                    subtotal=quantity * unit_price,
                )
            ]
            total_amount = quantity * unit_price
            payments = [PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED)]
            return intent, "Catalog Administration", line_items, payments, total_amount, 0.95

        # 2. Check Stock Adjustment / Damage Intent
        if any(w in t for w in ("kharab", "damage", "wastage", "nuksan", "lost", "shrinkage", "nastalau")):
            intent = TransactionIntent.INVENTORY_ADJUSTMENT
            qty_match = re.search(r"(\d+(?:\.\d+)?)", t)
            quantity = float(qty_match.group(1)) if qty_match else 1.0

            resolved_sku, matched_item, conf = self.inventory_service.resolve_sku(tenant_id, t)
            if matched_item:
                sku_id = matched_item.sku_id
                item_name = matched_item.item_name
                unit_price = matched_item.default_unit_price
                unit = matched_item.unit
                confidence = 0.95
            else:
                name_match = re.search(r"\d+\s*([a-zA-Z\s]+?)(?:kharab|damage|wastage|nuksan|lost)", t)
                extracted_name = name_match.group(1).strip().title() if name_match else "Damaged Item"
                sku_id = re.sub(r"[^a-zA-Z0-9]+", "-", extracted_name.lower()).strip("-") or "item-damaged"
                item_name = extracted_name
                unit_price = 200.0
                unit = "pieces"
                confidence = 0.70

            line_items = [
                LineItem(
                    sku_id=sku_id,
                    item_name=item_name,
                    quantity=quantity,
                    unit=unit,
                    unit_price=unit_price,
                    subtotal=quantity * unit_price,
                )
            ]
            total_amount = quantity * unit_price
            payments = [PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED)]
            return intent, "Internal Stock Adjustment", line_items, payments, total_amount, confidence

        # 3. Check Production Intent
        if any(w in t for w in ("banayi", "banaya", "production", "manufactured", "produce", "thayaru")):
            intent = TransactionIntent.PRODUCTION_LOG
            qty_match = re.search(r"(\d+(?:\.\d+)?)", t)
            qty = float(qty_match.group(1)) if qty_match else 50.0

            resolved_sku, matched_item, conf = self.inventory_service.resolve_sku(tenant_id, t)
            if matched_item:
                sku_id = matched_item.sku_id
                item_name = matched_item.item_name
                unit_price = matched_item.default_unit_price
                unit = matched_item.unit
                confidence = 0.95
            else:
                name_match = re.search(r"\d+\s*([a-zA-Z\s]+?)(?:banayi|banaya|produce|manufacture|thayaru|in workshop|workshop)", t)
                extracted_name = name_match.group(1).strip().title() if name_match else "Uncataloged Item"
                sku_id = re.sub(r"[^a-zA-Z0-9]+", "-", extracted_name.lower()).strip("-") or "item-uncataloged"
                item_name = extracted_name
                unit_price = 200.0
                unit = "pieces"
                confidence = 0.70  # Below 0.85 threshold to trigger Safety Gate

            line_items = [
                LineItem(
                    sku_id=sku_id,
                    item_name=item_name,
                    quantity=qty,
                    unit=unit,
                    unit_price=unit_price,
                    subtotal=qty * unit_price,
                )
            ]
            total_amount = qty * unit_price
            payments = [PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED)]
            return intent, party_name or "Internal Workshop", line_items, payments, total_amount, confidence

        # Default: Sale Invoice
        intent = TransactionIntent.SALE_INVOICE

        # Look for total amount or prices
        amounts = [float(x.replace(",", "")) for x in re.findall(r"(?:₹|rs\.?|inr)?\s*(\d+(?:,\d+)*(?:\.\d+)?)", t)]

        # Extract quantities
        qty_match = re.search(r"(\d+)\s*(cotton shirts?|shirts?|kurtas?|suit|pieces|meters|cycles?|bikes?|denim jackets?|jackets?|sarees?|[a-zA-Z]+)?", t)
        quantity = float(qty_match.group(1)) if qty_match else 1.0

        potential_amounts = [a for a in amounts if a >= 10.0 and a != quantity]
        if potential_amounts:
            total_amount = max(potential_amounts)
        else:
            total_amount = quantity * 200.0

        # Check split payments: cash, upi, udhaar
        cash_match = re.search(r"(\d+)\s*(?:rs|₹)?\s*(?:cash|rokhad|online|upi)", t)
        udhaar_match = re.search(r"(\d+)\s*(?:rs|₹)?\s*(?:udhaar|baaki|credit)", t)

        payments: List[PaymentNode] = []
        payment_stated = False
        if cash_match and udhaar_match:
            cash_amt = float(cash_match.group(1))
            udhaar_amt = float(udhaar_match.group(1))
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=cash_amt, status=PaymentStatus.SETTLED))
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=udhaar_amt, status=PaymentStatus.PENDING_COLLECTION))
            total_amount = cash_amt + udhaar_amt
            payment_stated = True
        elif cash_match and ("baaki udhaar" in t or "udhaar" in t):
            cash_amt = float(cash_match.group(1))
            udhaar_amt = max(0.0, total_amount - cash_amt)
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=cash_amt, status=PaymentStatus.SETTLED))
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=udhaar_amt, status=PaymentStatus.PENDING_COLLECTION))
            payment_stated = True
        elif "udhaar" in t:
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=total_amount, status=PaymentStatus.PENDING_COLLECTION))
            payment_stated = True
        elif any(w in t for w in ("cash", "rokhad", "online", "upi", "gpay", "phonepe")):
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED))
            payment_stated = True
        else:
            # Unstated payment: do not silently assume cash, mark low confidence
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED))
            confidence = min(confidence, 0.70)

        # SKU Resolution: Check merchant catalog first
        resolved_sku, matched_item, conf = self.inventory_service.resolve_sku(tenant_id, t)
        if matched_item:
            sku_id = matched_item.sku_id
            item_name = matched_item.item_name
            unit = matched_item.unit
            unit_price = total_amount / quantity if quantity > 0 else matched_item.default_unit_price
        else:
            # Dynamic extraction for uncataloged items (e.g. "Sold 3 cycles for 25000")
            item_match = re.search(r"(?:sold|bechi|becha|sale|diya)?\s*(\d+)\s+([a-zA-Z\s]+?)(?:\s+(?:for|to|ko|ki|ka|@|rs|₹|\d|cash|online|upi|udhaar)|$)", t)
            if item_match:
                extracted_name = item_match.group(2).strip().title()
            else:
                extracted_name = "Uncataloged Product"
            sku_id = re.sub(r"[^a-zA-Z0-9]+", "-", extracted_name.lower()).strip("-") or "item-uncataloged"
            item_name = extracted_name
            unit = "pieces"
            unit_price = total_amount / quantity if quantity > 0 else total_amount
            # Unknown product -> trigger Safety Gate
            confidence = min(confidence, 0.65)

        line_items = [
            LineItem(
                sku_id=sku_id,
                item_name=item_name,
                quantity=quantity,
                unit=unit,
                unit_price=unit_price,
                subtotal=total_amount,
            )
        ]

        if not party_name:
            if any(p.method == PaymentMethod.UDHAAR_CREDIT for p in payments):
                confidence = min(confidence, 0.70)

        return intent, party_name, line_items, payments, total_amount, confidence

    def _commit_ledger_transaction(self, txn: Transaction):
        """
        Applies atomic updates:
        1. Updates party udhaar balance.
        2. Adjusts inventory stock balance and logs inventory movement.
        """
        merchant_id = txn.merchant_id

        # 1. Update party udhaar balance
        udhaar_amount = sum(p.amount for p in txn.payments if p.method == PaymentMethod.UDHAAR_CREDIT)
        if udhaar_amount > 0 and txn.party_id:
            parties = self.parties.get(merchant_id, {})
            if txn.party_id in parties:
                parties[txn.party_id].current_udhaar_balance += udhaar_amount
                parties[txn.party_id].last_txn_at = datetime.now(timezone.utc)

        # 2. Update inventory stock via dedicated InventoryService
        for item in txn.line_items:
            sku = item.sku_id or "item-uncataloged"
            if txn.intent == TransactionIntent.INVENTORY_SETUP:
                self.inventory_service.create_or_update_item(
                    merchant_id=merchant_id,
                    item_name=item.item_name,
                    sku_id=sku,
                    unit=item.unit,
                    default_unit_price=item.unit_price,
                    initial_stock=item.quantity,
                )
            elif txn.intent == TransactionIntent.INVENTORY_ADJUSTMENT:
                self.inventory_service.record_adjustment(
                    merchant_id=merchant_id,
                    sku_id=sku,
                    quantity_delta=-item.quantity,
                    reason="Damage/Wastage",
                    reference_txn_id=txn.txn_id,
                )
            elif txn.intent == TransactionIntent.PRODUCTION_LOG:
                if sku not in self.inventory.get(merchant_id, {}):
                    self.inventory_service.create_or_update_item(
                        merchant_id=merchant_id,
                        item_name=item.item_name,
                        sku_id=sku,
                        unit=item.unit,
                        default_unit_price=item.unit_price,
                        initial_stock=0.0,
                    )
                self.inventory_service.record_production(
                    merchant_id=merchant_id,
                    sku_id=sku,
                    quantity=item.quantity,
                    reference_txn_id=txn.txn_id,
                )
            else:
                # Sale deduction
                if sku not in self.inventory.get(merchant_id, {}):
                    self.inventory_service.create_or_update_item(
                        merchant_id=merchant_id,
                        item_name=item.item_name,
                        sku_id=sku,
                        unit=item.unit,
                        default_unit_price=item.unit_price,
                        initial_stock=0.0,
                    )
                self.inventory_service.record_sale_deduction(
                    merchant_id=merchant_id,
                    sku_id=sku,
                    quantity=item.quantity,
                    reference_txn_id=txn.txn_id,
                )


_global_orchestrator = AgentOrchestrator()


def orchestrate_inbound_message(message: InboundMessage, *, media_bytes: bytes | None = None, conversation: dict | None = None):
    """Canonical input must come from the authenticated, merchant-resolved adapter.

    Result is ProcessingResult for extraction, PostingResult for commit, None for
    cancellation/errors. Transport dispatch occurs only after this function returns.
    """
    global pipeline, _global_orchestrator
    if message.message_type not in (MessageType.TEXT, MessageType.AUDIO, MessageType.IMAGE,
            MessageType.DOCUMENT, MessageType.INTERACTIVE_BUTTON, MessageType.INTERACTIVE_LIST):
        return None, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="Unsupported message type. Nothing posted.")
    durable_processing = conversation is not None
    conversation = conversation if conversation is not None else {}
    from app.services.conversation_language import preference, question
    language, script = preference(message.text_content or "", conversation)
    conversation.update(detected_language=language, script_preference=script)
    merchant = AuthenticatedMerchant(message.tenant_id, actor_phone=message.sender_phone)
    try:
        service = get_confirmation_service()
        if message.message_type == MessageType.TEXT and (message.text_content or "").strip().upper() in ("YES", "NO"):
            raise ConfirmationError("Use the Confirm or Cancel button for the specific candidate.")
        reply = service.parse_reply(merchant, message)
        if reply:
            candidate_id, action = reply
            if conversation.get("active_candidate_id") not in (None, candidate_id):
                raise ConfirmationError("Another candidate is active. Continue or cancel it first.")
            if action == "confirm" and conversation.get("active_candidate_id") == candidate_id:
                current = service.ledger.get_candidate(merchant.merchant_id, candidate_id)
                if conversation.get("candidate_revision") != str(current.revision):
                    raise ConfirmationError("Candidate changed. Review its latest revision in the dashboard.")
            result = service.handle_confirmation(merchant, candidate_id, action, message.message_id)
            conversation["last_candidate_id"] = candidate_id
            conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
            if isinstance(result, PostingResult):
                return result, posting_outbound(message.sender_phone, result)
            return None, OutboundMessage(recipient_phone=message.sender_phone,
                                         text_content=f"Posting status: {result}. Nothing posted.")
    except ConfirmationError as exc:
        return None, OutboundMessage(recipient_phone=message.sender_phone, text_content=str(exc))

    # Reporting requests must never enter financial extraction or clarify a sale.
    if message.message_type == MessageType.TEXT and re.search(
            r"\b(pdf|ledgers?|reports?)\b", message.text_content or "", re.IGNORECASE):
        # Check if it's a conversational query first
        q_out = _global_orchestrator._handle_query(message.text_content or "", message.tenant_id, message.sender_phone)
        if q_out:
            return None, q_out
        return None, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="Please use the authenticated dashboard for ledger reports and invoice downloads. Nothing posted.")

    # Check read-only queries & help before entering financial pipeline
    if message.message_type == MessageType.TEXT and not conversation.get("active_candidate_id"):
        query_out = _global_orchestrator._handle_query(message.text_content or "", message.tenant_id, message.sender_phone)
        if query_out:
            return None, query_out

    reviewing = None
    if conversation.get("active_candidate_id"):
        row = service.ledger.get_candidate(merchant.merchant_id, conversation["active_candidate_id"])
        if row.status != "AWAITING_CONFIRMATION" or row.expires_at <= service.clock():
            conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
            return None, OutboundMessage(recipient_phone=message.sender_phone,
                text_content="The pending candidate is closed or expired. Please resend your transaction.")
        if (message.text_content or "").strip().lower() == "continue":
            return None, OutboundMessage(recipient_phone=message.sender_phone,
                text_content=conversation.get("latest_question") or f"Review candidate {row.candidate_id} and use its Confirm or Cancel button.")
        if (not row.candidate.needs_clarification and not row.candidate.requires_review) or re.search(
                r"\b(sold|sell|sale|ammanu|ammnu|purchase|bought|stock|repaid|becha|bechi)\b|అమ్మ|बेच", message.text_content or "", re.I):
            return None, OutboundMessage(recipient_phone=message.sender_phone,
                text_content=f"Continue candidate {row.candidate_id} or cancel it before starting another transaction.",
                message_type=MessageType.INTERACTIVE_BUTTON, interactive_body=f"Continue the pending candidate {row.candidate_id}, or cancel it and resend the new transaction.",
                buttons=[CanonicalButton(id=button_id(row.candidate_id, "cancel"), title="Cancel pending")])
        reviewing = row
    raw_text = message.text_content
    if reviewing:
        raw_text = ("Clarify this same transaction using the merchant reply. Preserve buyer and items. "
                    "Leave unstated price and payment details null. Ask for remaining missing details. "
                    f"Use language {language} and script {script}. Ask one specific missing-field question at a time. "
                    f"Reassess missing_fields and clarification flags after each reply. "
                    f"Current facts: {reviewing.candidate.model_dump_json()}\nMerchant reply: {message.text_content}")
    if pipeline is None:
        from app.pipeline import PipelineService
        pipeline = PipelineService()
    result = pipeline.process_and_interpret(
        merchant_id=message.tenant_id, message_id=message.message_id,
        raw_text=raw_text, media_path=message.media_url,
        deduplicate=not durable_processing,
        media_bytes=media_bytes, mime_type=(message.media_mime_type or "").split(";")[0].strip().lower() or None)
    if result.input_metadata.is_duplicate:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Message already received. Duplicate ignored.")
    transactions = result.extraction.transactions
    if not transactions:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Could not process your request. Please send clear details.")
    if len(transactions) != 1:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Please send one transaction at a time. Nothing posted.")
    tx = transactions[0]
    if tx.schema_version != "1.1":
        return result, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="Unsupported extraction schema. Nothing posted.")
    if tx.transaction_type in ("query", "unknown"):
        return result, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="This request cannot post a transaction. Use the dashboard for reports. Nothing posted.")
    if tx.validation_status == "REJECTED":
        return result, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="❌ *Transaction Rejected*\n" + "\n".join(tx.validation_warnings))
    try:
        if reviewing:
            if tx.transaction_type != reviewing.candidate.transaction_type or tx.buyer_name != reviewing.candidate.buyer_name:
                raise ConfirmationError("Reply changed the buyer or transaction type. Review the candidate in the dashboard.")
            known = reviewing.candidate
            if len(tx.items) != len(known.items) or any(
                    getattr(old, field) is not None and getattr(old, field) != getattr(new, field)
                    for old, new in zip(known.items, tx.items)
                    for field in ("item_name", "quantity", "unit_price")):
                raise ConfirmationError("Reply changed known items. Continue or cancel the pending candidate.")
            if any(p not in tx.payments for p in known.payments):
                raise ConfirmationError("Reply changed known payments. Review the pending candidate.")
            row = service.ledger.replace_reviewed_candidate(
                merchant.merchant_id, reviewing.candidate_id, reviewing.revision,
                tx, message.text_content or "Media clarification", service.clock())
        else:
            row = service.stage(merchant, message.message_id, 0, tx)
        review = (row.candidate.validation_status == "NEEDS_REVIEW"
                  or row.candidate.needs_clarification or row.candidate.requires_review)
        latest_question, missing = question(row.candidate, language, script)
        conversation.update(active_candidate_id=row.candidate_id, candidate_revision=str(row.revision),
            expires_at=row.expires_at, missing_fields=missing, latest_question=latest_question if review else None)
        body = latest_question if review else "Please confirm these transaction details."
        body += f"\nParty: {row.candidate.buyer_name or 'N/A'}\n"
        for item in row.candidate.items:
            body += f"{item.item_name}: {item.quantity} × {item.unit_price}\n"
        for payment in row.candidate.payments:
            body += f"{payment.method}: {payment.amount}\n"
        body += f"Paid: {row.candidate.amount_paid}\nCredit: {row.candidate.credit_amount}\n"

        body += f"Total: {row.candidate.currency} {row.candidate.total_amount}\n"
        body += f"Candidate: {row.candidate_id}\nNothing posted yet."
        if review:
            body += "\nCorrection or explicit review and revalidation is required before Confirm can post."
        if review:
            return result, OutboundMessage(recipient_phone=message.sender_phone, text_content=body)
        return result, OutboundMessage(
            recipient_phone=message.sender_phone, message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header="Clarification Required" if review else "Confirm Transaction",
            interactive_body=body, interactive_footer="Clandexis",
            buttons=[CanonicalButton(id=button_id(row.candidate_id, "confirm"), title="✅ Confirm (हाँ)"),
                     CanonicalButton(id=button_id(row.candidate_id, "cancel"), title="❌ Cancel (रद्द)")])
    except ConfirmationError as exc:
        return result, OutboundMessage(recipient_phone=message.sender_phone, text_content=str(exc))
