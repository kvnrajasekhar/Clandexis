"""
Agent Orchestrator Service:
Bridges Inbound Webhook messages with Multimodal Gemini Extractor and Stage 4 Business Validator.
Maps candidate transactions into Clandexis Canonical and Firestore entity schemas.
"""

import logging
from typing import Optional, Tuple
from app.pipeline import PipelineService
from app.schemas.transaction import TransactionCandidate, ProcessingResult
from models.canonical import InboundMessage, OutboundMessage, CanonicalButton
from models.firestore_schema import TransactionIntent, TransactionStatus

logger = logging.getLogger("agent-orchestrator")

# Initialize multimodal pipeline
pipeline = PipelineService()

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

def orchestrate_inbound_message(message: InboundMessage) -> Tuple[ProcessingResult, OutboundMessage]:
    """
    Process inbound message from Meta WhatsApp:
    1. Extract multimodal candidate via Gemini 3.5/3.8 Flash.
    2. Run Stage 4 Business Validation.
    3. Generate appropriate OutboundMessage (Confirmation / Clarification Button / Error).
    """
    merchant_id = message.tenant_id
    sender_phone = message.sender_phone
    
    # 1. Execute Pipeline
    result = pipeline.process_and_interpret(
        merchant_id=merchant_id,
        message_id=message.message_id,
        raw_text=message.text_content,
        media_path=message.media_url,  # Direct path if downloaded locally
    )
    
    tx: TransactionCandidate = result.extraction.transactions[0] if result.extraction.transactions else None
    
    if not tx:
        outbound = OutboundMessage(
            recipient_phone=sender_phone,
            text_content="⚠️ Could not process your request. Please try again with clear voice or text."
        )
        return result, outbound

    # 2. Check for Duplicate
    if result.input_metadata.is_duplicate:
        outbound = OutboundMessage(
            recipient_phone=sender_phone,
            text_content="ℹ️ Message already received and processed. Duplicate ignored."
        )
        return result, outbound

    # 3. Formulate WhatsApp Response based on Validation Status
    if tx.validation_status == "REJECTED":
        warning_text = "\n".join(f"• {w}" for w in tx.validation_warnings)
        outbound = OutboundMessage(
            recipient_phone=sender_phone,
            text_content=f"❌ *Transaction Rejected*\n{warning_text}\n\nPlease check the amounts and send again."
        )
    elif tx.needs_clarification or tx.validation_status == "NEEDS_REVIEW":
        q_text = tx.clarification_question or "Please confirm the transaction details before we record it."
        warning_str = f"\n_Note: {tx.validation_warnings[0]}_" if tx.validation_warnings else ""
        
        # Dispatch 1-Tap Quick-Reply Confirmation buttons
        outbound = OutboundMessage(
            recipient_phone=sender_phone,
            interactive_header="⚠️ Clarification Required",
            interactive_body=f"{q_text}{warning_str}\n\n*Party:* {tx.buyer_name or 'N/A'}\n*Total:* ₹{tx.total_amount or 0:.2f}",
            interactive_footer="Clandexis Invisible ERP",
            buttons=[
                CanonicalButton(id=f"CONFIRM_{message.message_id}", title="✅ Confirm (हाँ)"),
                CanonicalButton(id=f"CANCEL_{message.message_id}", title="❌ Cancel (रद्द)"),
            ]
        )
    else:
        # ACCEPTED: Clean validated transaction candidate ready for ledger commit
        items_summary = "\n".join(
            f"• {i.quantity or 1} {i.item_name} @ ₹{i.unit_price or 0:.2f} = ₹{i.line_total or 0:.2f}"
            for i in tx.items
        ) if tx.items else "• General Entry"
        
        paid_info = f"₹{tx.amount_paid:.2f}" if tx.amount_paid is not None else "₹0.00"
        credit_info = f"₹{tx.credit_amount:.2f}" if tx.credit_amount is not None else "₹0.00"

        outbound = OutboundMessage(
            recipient_phone=sender_phone,
            text_content=(
                f"✅ *Recorded {tx.transaction_type.capitalize()}*\n"
                f"*Party:* {tx.buyer_name or 'Direct Customer'}\n"
                f"{items_summary}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"*Total:* ₹{tx.total_amount or 0:.2f}\n"
                f"*Paid:* {paid_info}\n"
                f"*Balance Due (Udhaar):* {credit_info}\n\n"
                f"_Entry posted to ledger successfully._"
            )
        )
        
    return result, outbound
