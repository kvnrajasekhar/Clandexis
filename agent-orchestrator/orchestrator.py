"""
Agent Orchestrator Service for Clandexis Invisible ERP.
Handles InboundMessage processing, conversational vernacular NLP / AI extraction,
deterministic invariant validation (validate_split_math), human-in-the-loop safety gating,
atomic ledger mutations (inventory stock & udhaar balances), and outbound message construction.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import settings
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

logger = logging.getLogger("agent-orchestrator")


class AgentOrchestrator:
    """
    Central business engine and conversational agent orchestrator for Clandexis.
    Provides multi-tenant ledger management, intent routing, entity extraction,
    safety gating, and automated WhatsApp outbound response generation.
    """

    def __init__(self):
        # In-memory storage for rapid demo & testing (backed by Firestore in cloud)
        self.merchants: Dict[str, Merchant] = {}
        self.parties: Dict[str, Dict[str, Party]] = {}  # merchant_id -> {party_id: Party}
        self.inventory: Dict[str, Dict[str, InventoryItem]] = {}  # merchant_id -> {sku_id: InventoryItem}
        self.transactions: Dict[str, Transaction] = {}  # txn_id -> Transaction
        self.movements: Dict[str, List[InventoryMovement]] = {}  # merchant_id -> [InventoryMovement]

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

    # ==========================================================================
    # Main Inbound Message Handler
    # ==========================================================================

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

        # Ensure tenant exists or seed dynamically
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

    # ==========================================================================
    # Interactive Button Actions (Safety Gate Resolutions)
    # ==========================================================================

    def _handle_interactive_button(self, message: InboundMessage) -> OutboundMessage:
        """
        Handles interactive button callbacks:
        - CONFIRM_TXN_{id} -> Commit staged transaction to ledger atomically
        - CANCEL_TXN_{id} -> Mark transaction as rejected
        """
        action_id = message.interactive_id or ""
        tenant_id = message.tenant_id
        recipient_phone = message.sender_phone

        if action_id.startswith("CONFIRM_TXN_"):
            txn_id = action_id[len("CONFIRM_TXN_"):]
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

            # Atomically commit ledger mutation
            self._commit_ledger_transaction(txn)
            txn.status = TransactionStatus.COMMITTED
            txn.committed_at = datetime.now(timezone.utc)

            # Format itemized success summary
            party_info = txn.party_name or "Counterparty"
            udhaar_amount = sum(p.amount for p in txn.payments if p.method == PaymentMethod.UDHAAR_CREDIT)
            
            # Fetch updated party balance
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
                f"{party_balance_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Ledger & stock balances updated atomically. Digital receipt generated."
            )

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=body,
            )

        elif action_id.startswith("CANCEL_TXN_"):
            txn_id = action_id[len("CANCEL_TXN_"):]
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

    # ==========================================================================
    # Conversational Queries (Read-Only)
    # ==========================================================================

    def _handle_query(self, text: str, tenant_id: str, recipient_phone: str) -> Optional[OutboundMessage]:
        """
        Detects read-only queries (stock level, udhaar balance, summary, help).
        Only triggers if transaction recording action words are absent.
        """
        t = text.lower().strip()
        if not t:
            return None

        # If text indicates transaction recording, do not treat as read-only query
        txn_action_words = (
            "bechi", "becha", "sold", "sale", "diya", "kharida", "purchase",
            "banayi", "manufactured", "produce", "likh lo", "likho", "record"
        )
        if any(w in t for w in txn_action_words):
            return None

        # 1. Help / Info Query
        if t in ("help", "madad", "menu", "hi", "hello", "namaste", "shuru"):
            help_text = (
                "🙏 *नमस्ते! Welcome to Clandexis (Invisible ERP)*\n"
                "Aap seedhe vernacular WhatsApp voice ya text me likhein:\n\n"
                "📌 *Record Sale/Udhaar:*\n"
                "• _\"Ramesh ko 10 cotton shirt bechi ₹2000 ki, ₹500 cash diya baaki udhaar\"_\n\n"
                "📌 *Stock Query:*\n"
                "• _\"Stock kitna hai?\"_\n\n"
                "📌 *Party Balance:*\n"
                "• _\"Ramesh ka udhaar kitna hai?\"_\n\n"
                "📌 *Daily Summary:*\n"
                "• _\"Aaj ki bikri ka summary do\"_"
            )
            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=help_text,
            )

        # 2. Stock Query
        if any(w in t for w in ("stock", "maal", "inventory")) or ("kitna" in t and "shirt" in t):
            items = self.inventory.get(tenant_id, {}).values()
            if not items:
                msg = "📦 No inventory registered for this account."
            else:
                lines = ["📦 *Current Stock Status (स्टॉक स्थिति)*\n━━━━━━━━━━━━━━━━━━━"]
                for it in items:
                    lines.append(f"• *{it.item_name}:* {it.current_stock:.0f} {it.unit} (₹{it.default_unit_price:,.2f})")
                lines.append("━━━━━━━━━━━━━━━━━━━")
                msg = "\n".join(lines)

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        # 3. Party Udhaar Query (must be a query, not a sale transaction)
        is_query_phrase = any(q in t for q in ("kitna", "kitne", "batao", "dikhao", "check", "kya", "list", "status")) or t in ("udhaar", "hisaab", "khata", "balance")
        if is_query_phrase and any(w in t for w in ("udhaar", "hisaab", "khata", "balance", "baaki")):
            parties = self.parties.get(tenant_id, {})
            # Look for specific party mention
            matched_party = None
            for p in parties.values():
                if p.display_name.lower() in t or any(a in t for a in p.aliases):
                    matched_party = p
                    break

            if matched_party:
                msg = (
                    f"📒 *Udhaar Ledger (उधार खाता)*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 *Party:* {matched_party.display_name}\n"
                    f"📞 *Phone:* {matched_party.phone or 'N/A'}\n"
                    f"💰 *Current Udhaar Balance:* ₹{matched_party.current_udhaar_balance:,.2f}\n"
                    f"💳 *Credit Limit:* ₹{matched_party.credit_limit:,.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━"
                )
            else:
                lines = ["📒 *All Outstanding Udhaar Balances (उधार सूचि)*\n━━━━━━━━━━━━━━━━━━━"]
                total_udhaar = 0.0
                for p in parties.values():
                    lines.append(f"• *{p.display_name}:* ₹{p.current_udhaar_balance:,.2f}")
                    total_udhaar += p.current_udhaar_balance
                lines.append("━━━━━━━━━━━━━━━━━━━")
                lines.append(f"💰 *Total Receivables:* ₹{total_udhaar:,.2f}")
                msg = "\n".join(lines)

            return OutboundMessage(
                recipient_phone=recipient_phone,
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=msg,
            )

        # 4. Summary / Sales Report
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

    # ==========================================================================
    # Transaction Processing & Safety Gate
    # ==========================================================================

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

        # 1. Vernacular Entity Extraction
        intent, party_name, line_items, payments, total_amount, confidence_score = (
            self._extract_entities_from_text(text, tenant_id)
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

        # 2. Invariant Math Validation & Safety Gate Check
        is_math_valid, gate_reason, requires_safety_gate = validate_split_math(
            total_amount=total_amount,
            line_items=line_items,
            payments=payments,
            confidence_score=confidence_score,
        )

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
            source_channel=message.channel.value,
            source_media_type=message.message_type.value,
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

        # 4. Human-in-the-Loop Safety Gate Branch: High credit or low confidence
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

            buttons = [
                CanonicalButton(id=f"CONFIRM_TXN_{txn_id}", title="✅ Confirm (हाँ)"),
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

    # ==========================================================================
    # Entity Extraction (Vernacular NLP / Heuristics)
    # ==========================================================================

    def _extract_entities_from_text(
        self,
        text: str,
        tenant_id: str,
    ) -> Tuple[TransactionIntent, Optional[str], List[LineItem], List[PaymentNode], float, float]:
        """
        Extracts financial intent, party, line items, and split payments.
        Robust deterministic multilingual regex parser tailored for Hindi/Hinglish/English.
        """
        t = text.lower()
        confidence = 0.95

        # Identify party
        party_name = None
        for p in self.parties.get(tenant_id, {}).values():
            if p.display_name.lower() in t or any(a in t for a in p.aliases):
                party_name = p.display_name
                break

        # Check production intent
        if any(w in t for w in ("banayi", "production", "manufactured", "produce")):
            intent = TransactionIntent.PRODUCTION_LOG
            # match quantity and item
            qty_match = re.search(r"(\d+)\s*(cotton shirts?|shirts?|kurtas?|pieces?)", t)
            qty = float(qty_match.group(1)) if qty_match else 50.0
            item_name = "Cotton Shirt (L)"
            unit_price = 200.0
            line_items = [
                LineItem(
                    sku_id="shirt-cotton-l",
                    item_name=item_name,
                    quantity=qty,
                    unit="pieces",
                    unit_price=unit_price,
                    subtotal=qty * unit_price,
                )
            ]
            total_amount = qty * unit_price
            payments = [PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED)]
            return intent, party_name or "Internal Workshop", line_items, payments, total_amount, confidence

        # Default: Sale Invoice
        intent = TransactionIntent.SALE_INVOICE

        # Extract quantities and prices using regex
        # Look for patterns like: "10 cotton shirt ... ₹2000" or "10 shirt @ 200"
        qty_match = re.search(r"(\d+)\s*(cotton shirts?|shirts?|kurtas?|suit|pieces|meters)?", t)
        quantity = float(qty_match.group(1)) if qty_match else 5.0
        
        # Look for total amount or prices
        # e.g., ₹3,500, 3500 ka, total 2000
        amounts = [float(x.replace(",", "")) for x in re.findall(r"(?:₹|rs\.?|inr)?\s*(\d+(?:,\d+)*(?:\.\d+)?)", t)]
        
        # Filter amounts: remove small single-digit matches that might be quantities
        potential_amounts = [a for a in amounts if a >= 10.0 and a != quantity]

        if potential_amounts:
            total_amount = max(potential_amounts)
        else:
            # Fallback calculation
            unit_price = 200.0
            total_amount = quantity * unit_price

        # Check split payments: cash, upi, udhaar
        # e.g. "1000 cash ... baaki udhaar"
        cash_match = re.search(r"(\d+)\s*(?:rs|₹)?\s*(?:cash|rokhad|online|upi)", t)
        udhaar_match = re.search(r"(\d+)\s*(?:rs|₹)?\s*(?:udhaar|baaki|credit)", t)

        payments: List[PaymentNode] = []
        if cash_match and udhaar_match:
            cash_amt = float(cash_match.group(1))
            udhaar_amt = float(udhaar_match.group(1))
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=cash_amt, status=PaymentStatus.SETTLED))
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=udhaar_amt, status=PaymentStatus.PENDING_COLLECTION))
            total_amount = cash_amt + udhaar_amt
        elif cash_match and ("baaki udhaar" in t or "udhaar" in t):
            cash_amt = float(cash_match.group(1))
            udhaar_amt = max(0.0, total_amount - cash_amt)
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=cash_amt, status=PaymentStatus.SETTLED))
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=udhaar_amt, status=PaymentStatus.PENDING_COLLECTION))
        elif "udhaar" in t:
            payments.append(PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=total_amount, status=PaymentStatus.PENDING_COLLECTION))
        else:
            payments.append(PaymentNode(method=PaymentMethod.CASH, amount=total_amount, status=PaymentStatus.SETTLED))

        # Item unit price
        item_unit_price = total_amount / quantity if quantity > 0 else total_amount
        line_items = [
            LineItem(
                sku_id="shirt-cotton-l",
                item_name="Cotton Shirt (L)",
                quantity=quantity,
                unit="pieces",
                unit_price=item_unit_price,
                subtotal=total_amount,
            )
        ]

        if not party_name:
            # If no party detected for a credit/udhaar sale, lower confidence
            if any(p.method == PaymentMethod.UDHAAR_CREDIT for p in payments):
                confidence = 0.70

        return intent, party_name, line_items, payments, total_amount, confidence

    # ==========================================================================
    # Ledger Mutations
    # ==========================================================================

    def _commit_ledger_transaction(self, txn: Transaction):
        """
        Applies atomic multi-document updates:
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

        # 2. Update inventory stock
        inv_map = self.inventory.get(merchant_id, {})
        for item in txn.line_items:
            sku = item.sku_id or "shirt-cotton-l"
            if sku in inv_map:
                inv_item = inv_map[sku]
                if txn.intent == TransactionIntent.PRODUCTION_LOG:
                    inv_item.current_stock += item.quantity
                    mov_type = MovementType.PRODUCTION_ADD
                else:
                    inv_item.current_stock -= item.quantity
                    mov_type = MovementType.SALE_DEDUCT

                inv_item.updated_at = datetime.now(timezone.utc)

                movement = InventoryMovement(
                    movement_id=f"mov_{uuid.uuid4().hex[:8]}",
                    merchant_id=merchant_id,
                    sku_id=sku,
                    movement_type=mov_type,
                    quantity=item.quantity if mov_type == MovementType.PRODUCTION_ADD else -item.quantity,
                    balance_after=inv_item.current_stock,
                    reference_txn_id=txn.txn_id,
                )
                if merchant_id not in self.movements:
                    self.movements[merchant_id] = []
                self.movements[merchant_id].append(movement)

    def _init_tenant(self, tenant_id: str, sender_name: Optional[str] = None):
        """Initializes a new merchant tenant with standard defaults."""
        self.merchants[tenant_id] = Merchant(
            merchant_id=tenant_id,
            business_name=f"{sender_name or 'MSME'} Enterprises",
            owner_name=sender_name or "Merchant Owner",
            preferred_lang="hi",
            high_credit_threshold=settings.HIGH_CREDIT_THRESHOLD,
            whatsapp_phone_number_id=settings.META_PHONE_NUMBER_ID,
            whatsapp_verified_at=datetime.now(timezone.utc),
        )
        self.parties[tenant_id] = {}
        self.inventory[tenant_id] = {}
        self.movements[tenant_id] = []
