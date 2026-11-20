"""Canonical WhatsApp orchestration over persistent candidate and ledger services."""

import re

from app.services.confirmation import (
    AuthenticatedMerchant,
    ConfirmationError,
    PostingResult,
    button_id,
    get_confirmation_service,
)
from app.services.query_service import QueryService
from models.canonical import (
    CanonicalButton,
    InboundMessage,
    MessageType,
    OutboundMessage,
)

pipeline = None  # Lazy: callbacks and read-only queries never initialize Gemini.
query_service = QueryService()


def posting_outbound(phone: str, result: PostingResult) -> OutboundMessage:
    balances = ", ".join(
        f"{sku}: {quantity}" for sku, quantity in result.inventory_balance.items()
    )
    return OutboundMessage(
        recipient_phone=phone,
        text_content=(
            f"Posting status: {result.posting_status}\n"
            f"Transaction: {result.transaction_id}\n"
            f"Amount paid: {result.amount_paid}\n"
            f"Credit outstanding: {result.credit_outstanding}\n"
            f"Inventory balance: {balances or 'No inventory movement'}"
        ),
    )


def orchestrate_inbound_message(
    message: InboundMessage,
    *,
    media_bytes: bytes | None = None,
    conversation: dict | None = None,
):
    """Process one authenticated inbound message without transport side effects."""
    global pipeline
    if message.message_type not in (
        MessageType.TEXT,
        MessageType.AUDIO,
        MessageType.IMAGE,
        MessageType.DOCUMENT,
        MessageType.INTERACTIVE_BUTTON,
        MessageType.INTERACTIVE_LIST,
    ):
        return None, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="Unsupported message type. Nothing posted.",
        )

    durable_processing = conversation is not None
    conversation = conversation if conversation is not None else {}
    from app.services.conversation_language import preference, question

    language, script = preference(message.text_content or "", conversation)
    conversation.update(detected_language=language, script_preference=script)
    merchant = AuthenticatedMerchant(
        message.tenant_id, actor_phone=message.sender_phone
    )
    try:
        service = get_confirmation_service()
    except ConfirmationError as exc:
        return None, OutboundMessage(
            recipient_phone=message.sender_phone, text_content=str(exc)
        )

    try:
        if message.message_type == MessageType.TEXT and (
            message.text_content or ""
        ).strip().upper() in ("YES", "NO"):
            raise ConfirmationError(
                "Use the Confirm or Cancel button for the specific candidate."
            )
        reply = service.parse_reply(merchant, message)
        if reply:
            candidate_id, action = reply
            if conversation.get("active_candidate_id") not in (None, candidate_id):
                raise ConfirmationError(
                    "Another candidate is active. Continue or cancel it first."
                )
            if (
                action == "confirm"
                and conversation.get("active_candidate_id") == candidate_id
            ):
                current = service.ledger.get_candidate(
                    merchant.merchant_id, candidate_id
                )
                if conversation.get("candidate_revision") != str(current.revision):
                    raise ConfirmationError(
                        "Candidate changed. Review its latest revision in the dashboard."
                    )
            result = service.handle_confirmation(
                merchant, candidate_id, action, message.message_id
            )
            conversation["last_candidate_id"] = candidate_id
            conversation.update(
                active_candidate_id=None,
                latest_question=None,
                missing_fields=[],
                candidate_revision=None,
                expires_at=None,
            )
            if isinstance(result, PostingResult):
                return result, posting_outbound(message.sender_phone, result)
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=f"Posting status: {result}. Nothing posted.",
            )
    except ConfirmationError as exc:
        return None, OutboundMessage(
            recipient_phone=message.sender_phone, text_content=str(exc)
        )

    # Queries are read-only and intentionally do not alter pending candidate state.
    if message.message_type == MessageType.TEXT:
        clean_text = (message.text_content or "").strip().lower()
        if clean_text in ("hi", "hello", "hey", "namaste", "namaskaram", "start", "help"):
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=(
                    "👋 *Hello! Welcome to Clandexis Invisible ERP.*\n\n"
                    "You can record transactions using Voice Notes, Bill Photos, or Text:\n"
                    "• *Sale*: 'Sold 20 Cotton Shirt to Ramesh for 4000 cash, 2000 credit'\n"
                    "• *Stock*: 'stock status' or 'maal kitna hai'\n"
                    "• *Khata/Balance*: 'Ramesh balance' or 'udhaar dikhao'\n\n"
                    "Try sending an audio note or typing a transaction!"
                ),
            )
        query = query_service.handle(
            merchant,
            message.text_content or "",
            message.sender_phone,
            language=language,
            script=script,
        )
        if query:
            return None, query

    reviewing = None
    if conversation.get("active_candidate_id"):
        row = service.ledger.get_candidate(
            merchant.merchant_id, conversation["active_candidate_id"]
        )
        if row.status != "AWAITING_CONFIRMATION" or row.expires_at <= service.clock():
            conversation.update(
                active_candidate_id=None,
                latest_question=None,
                missing_fields=[],
                candidate_revision=None,
                expires_at=None,
            )
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=(
                    "The pending candidate is closed or expired. "
                    "Please resend your transaction."
                ),
            )
        if (message.text_content or "").strip().lower() == "continue":
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=conversation.get("latest_question")
                or (
                    f"Review candidate {row.candidate_id} and use its "
                    "Confirm or Cancel button."
                ),
            )
        if (
            not row.candidate.needs_clarification and not row.candidate.requires_review
        ) or re.search(
            r"\b(sold|sell|sale|ammanu|ammnu|purchase|bought|stock|repaid|becha|bechi)\b|అమ్మ|बेच",
            message.text_content or "",
            re.I,
        ):
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=(
                    f"Continue candidate {row.candidate_id} or cancel it before "
                    "starting another transaction."
                ),
                message_type=MessageType.INTERACTIVE_BUTTON,
                interactive_body=(
                    f"Continue the pending candidate {row.candidate_id}, or cancel "
                    "it and resend the new transaction."
                ),
                buttons=[
                    CanonicalButton(
                        id=button_id(row.candidate_id, "cancel"),
                        title="Cancel pending",
                    )
                ],
            )
        reviewing = row

    raw_text = message.text_content
    if reviewing:
        raw_text = (
            "Clarify this same transaction using the merchant reply. Preserve buyer "
            "and items. Leave unstated price and payment details null. Ask for "
            "remaining missing details. "
            f"Use language {language} and script {script}. Ask one specific "
            "missing-field question at a time. Reassess missing_fields and "
            "clarification flags after each reply. "
            f"Current facts: {reviewing.candidate.model_dump_json()}\n"
            f"Merchant reply: {message.text_content}"
        )
    if pipeline is None:
        from app.pipeline import PipelineService

        pipeline = PipelineService()
    result = pipeline.process_and_interpret(
        merchant_id=message.tenant_id,
        message_id=message.message_id,
        raw_text=raw_text,
        media_path=message.media_url,
        deduplicate=not durable_processing,
        media_bytes=media_bytes,
        mime_type=(message.media_mime_type or "").split(";")[0].strip().lower() or None,
    )
    if result.input_metadata.is_duplicate:
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="Message already received. Duplicate ignored.",
        )
    transactions = result.extraction.transactions
    if not transactions:
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="Could not process your request. Please send clear details.",
        )
    if len(transactions) != 1:
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="Please send one transaction at a time. Nothing posted.",
        )
    tx = transactions[0]
    if tx.schema_version != "1.1":
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="Unsupported extraction schema. Nothing posted.",
        )
    if tx.transaction_type in ("query", "unknown"):
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content=(
                "This request cannot post a transaction. Use the dashboard for "
                "unsupported reports. Nothing posted."
            ),
        )
    if tx.validation_status == "REJECTED":
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="❌ *Transaction Rejected*\n"
            + "\n".join(tx.validation_warnings),
        )

    try:
        if reviewing:
            if (
                tx.transaction_type != reviewing.candidate.transaction_type
                or tx.buyer_name != reviewing.candidate.buyer_name
            ):
                raise ConfirmationError(
                    "Reply changed the buyer or transaction type. "
                    "Review the candidate in the dashboard."
                )
            known = reviewing.candidate
            if len(tx.items) != len(known.items) or any(
                getattr(old, field) is not None
                and getattr(old, field) != getattr(new, field)
                for old, new in zip(known.items, tx.items)
                for field in ("item_name", "quantity", "unit_price")
            ):
                raise ConfirmationError(
                    "Reply changed known items. Continue or cancel the pending candidate."
                )
            if any(payment not in tx.payments for payment in known.payments):
                raise ConfirmationError(
                    "Reply changed known payments. Review the pending candidate."
                )
            row = service.ledger.replace_reviewed_candidate(
                merchant.merchant_id,
                reviewing.candidate_id,
                reviewing.revision,
                tx,
                message.text_content or "Media clarification",
                service.clock(),
            )
        else:
            row = service.stage(merchant, message.message_id, 0, tx)
        review = (
            row.candidate.validation_status == "NEEDS_REVIEW"
            or row.candidate.needs_clarification
            or row.candidate.requires_review
        )
        latest_question, missing = question(row.candidate, language, script)
        conversation.update(
            active_candidate_id=row.candidate_id,
            candidate_revision=str(row.revision),
            expires_at=row.expires_at,
            missing_fields=missing,
            latest_question=latest_question if review else None,
        )
        body = (
            latest_question if review else "Please confirm these transaction details."
        )
        body += f"\nParty: {row.candidate.buyer_name or 'N/A'}\n"
        for item in row.candidate.items:
            body += f"{item.item_name}: {item.quantity} × {item.unit_price}\n"
        for payment in row.candidate.payments:
            body += f"{payment.method}: {payment.amount}\n"
        body += f"Paid: {row.candidate.amount_paid}\n"
        body += f"Credit: {row.candidate.credit_amount}\n"
        body += f"Total: {row.candidate.currency} {row.candidate.total_amount}\n"
        body += f"Candidate: {row.candidate_id}\nNothing posted yet."
        if review:
            body += (
                "\nCorrection or explicit review and revalidation is required "
                "before Confirm can post."
            )
            return result, OutboundMessage(
                recipient_phone=message.sender_phone, text_content=body
            )
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header="Confirm Transaction",
            interactive_body=body,
            interactive_footer="Clandexis",
            buttons=[
                CanonicalButton(
                    id=button_id(row.candidate_id, "confirm"),
                    title="✅ Confirm (हाँ)",
                ),
                CanonicalButton(
                    id=button_id(row.candidate_id, "cancel"),
                    title="❌ Cancel (रद्द)",
                ),
            ],
        )
    except ConfirmationError as exc:
        return result, OutboundMessage(
            recipient_phone=message.sender_phone, text_content=str(exc)
        )
