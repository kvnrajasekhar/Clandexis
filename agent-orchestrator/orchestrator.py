"""Route replies before extraction; stage persisted candidates before approval."""
import logging
from app.schemas.transaction import ProcessingResult
from app.services.confirmation import (
    AuthenticatedMerchant, ConfirmationError, PostingResult, button_id,
    get_confirmation_service,
)
from models.canonical import InboundMessage, OutboundMessage, CanonicalButton, MessageType
from models.firestore_schema import TransactionIntent

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
    return OutboundMessage(recipient_phone=phone, text_content=(
        f"Posting status: {result.posting_status}\nTransaction: {result.transaction_id}\n"
        f"Amount paid: {result.amount_paid}\nCredit outstanding: {result.credit_outstanding}\n"
        f"Inventory balance: {balances or 'No inventory movement'}"))


def orchestrate_inbound_message(message: InboundMessage):
    """Canonical input must come from the authenticated, merchant-resolved adapter.

    Result is ProcessingResult for extraction, PostingResult for commit, None for
    cancellation/errors. Transport dispatch occurs only after this function returns.
    """
    global pipeline
    merchant = AuthenticatedMerchant(message.tenant_id)
    try:
        service = get_confirmation_service()
        reply = service.parse_reply(merchant, message)
        if reply:
            candidate_id, action = reply
            result = service.handle_confirmation(merchant, candidate_id, action, message.message_id)
            if isinstance(result, PostingResult):
                return result, posting_outbound(message.sender_phone, result)
            return None, OutboundMessage(recipient_phone=message.sender_phone,
                                         text_content=f"Posting status: {result}. Nothing posted.")
    except ConfirmationError as exc:
        return None, OutboundMessage(recipient_phone=message.sender_phone, text_content=str(exc))

    if pipeline is None:
        from app.pipeline import PipelineService
        pipeline = PipelineService()
    result = pipeline.process_and_interpret(
        merchant_id=message.tenant_id, message_id=message.message_id,
        raw_text=message.text_content, media_path=message.media_url)
    if result.input_metadata.is_duplicate:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Message already received. Duplicate ignored.")
    transactions = result.extraction.transactions
    if not transactions:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Could not process your request. Please send clear details.")
    # Do not silently discard additional extracted transactions or approve a bundle
    # using one button. Ask for individual submissions until transport supports a batch.
    if len(transactions) != 1:
        return result, OutboundMessage(recipient_phone=message.sender_phone,
                                       text_content="Please send one transaction at a time. Nothing posted.")
    tx = transactions[0]
    if tx.validation_status == "REJECTED":
        return result, OutboundMessage(recipient_phone=message.sender_phone,
            text_content="❌ *Transaction Rejected*\n" + "\n".join(tx.validation_warnings))
    try:
        row = service.stage(merchant, message.message_id, 0, tx)
        review = (row.candidate.validation_status == "NEEDS_REVIEW"
                  or row.candidate.needs_clarification or row.candidate.requires_review)
        body = (row.candidate.clarification_question or "Review these details before confirming.")
        body += f"\nParty: {row.candidate.buyer_name or 'N/A'}\n"
        body += f"Total: {row.candidate.currency} {row.candidate.total_amount}\n"
        body += f"Candidate: {row.candidate_id}\nNothing posted yet."
        if review:
            body += "\nCorrection or explicit review and revalidation is required before Confirm can post."
        return result, OutboundMessage(
            recipient_phone=message.sender_phone, message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header="Clarification Required" if review else "Confirm Transaction",
            interactive_body=body, interactive_footer="Clandexis",
            buttons=[CanonicalButton(id=button_id(row.candidate_id, "confirm"), title="✅ Confirm (हाँ)"),
                     CanonicalButton(id=button_id(row.candidate_id, "cancel"), title="❌ Cancel (रद्द)")])
    except ConfirmationError as exc:
        return result, OutboundMessage(recipient_phone=message.sender_phone, text_content=str(exc))
