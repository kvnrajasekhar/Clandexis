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
from app.services.whatsapp_response_formatter import (
    candidate_summary,
    copy_for,
    hold_message,
    posting_summary,
)
from models.canonical import (
    CanonicalButton,
    InboundMessage,
    MessageType,
    OutboundMessage,
)

pipeline = None  # Lazy: callbacks and read-only queries never initialize Gemini.
query_service = QueryService()


def posting_outbound(phone: str, result: PostingResult, language: str = "en",
                     script: str = "Latin") -> OutboundMessage:
    labels = copy_for(language, script)
    body = posting_summary(result, language, script)
    return OutboundMessage(
        recipient_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_header=f"✅ {labels.get('posted', 'Transaction recorded')}",
        interactive_body=body,
        interactive_footer="Clandexis Invisible ERP",
        buttons=[
            CanonicalButton(
                id=f"view_pdf_{result.transaction_id}",
                title="📄 PDF Invoice",
            ),
            CanonicalButton(
                id=f"view_ledger_{result.transaction_id}",
                title="📊 Party Ledger",
            ),
        ],
        text_content=body,
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
    from app.services.conversation_language import preference, question, resolve_language_and_script, SUPPORTED_LANGUAGES

    is_audio = (
        message.message_type == MessageType.AUDIO
        or media_bytes is not None
        or bool(message.media_url)
    )
    language, script = preference(message.text_content or "", conversation)

    # If message has no text (e.g. voice note/image) or language is default, hydrate from merchant profile in DB
    if not message.text_content or conversation.get("detected_language") in (None, "en"):
        from config import settings
        if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
            try:
                import psycopg
                with psycopg.connect(settings.DATABASE_URL, connect_timeout=3) as conn:
                    row = conn.execute(
                        "SELECT preferred_language, script_preference FROM merchants WHERE phone_e164 = %s LIMIT 1",
                        (message.sender_phone,)
                    ).fetchone()
                    if row and row[0]:
                        language = row[0]
                        script = row[1] or SUPPORTED_LANGUAGES.get(row[0], {}).get("default_script", "Latin")
            except Exception:
                pass

    if not is_audio and (message.text_content or "").strip():
        conversation.update(detected_language=language, script_preference=script)
    elif not conversation.get("detected_language"):
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

    # Handle retry action on rejected transactions cleanly
    if message.interactive_id and message.interactive_id.startswith("retry_"):
        conversation.update(
            active_candidate_id=None,
            latest_question=None,
            missing_fields=[],
            candidate_revision=None,
            expires_at=None,
        )
        retry_prompts = {
            "te": "సరే, దయచేసి లావాదేవీ వివరాలను మళ్లీ వాయిస్ లేదా టెక్స్ట్ ద్వారా పంపండి.",
            "hi": "ठीक है, कृपया लेन-देन का विवरण फिर से बोलकर या लिखकर भेजें।",
            "ta": "சரி, உங்கள் பரிவர்த்தனை விவரங்களை மீண்டும் குரல் அல்லது செய்தியாக அனுப்பவும்.",
            "en": "Sure, please resend your transaction details by voice note or text."
        }
        return None, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content=retry_prompts.get(language, retry_prompts["en"]),
        )

    try:
        if message.message_type == MessageType.TEXT and (
            message.text_content or ""
        ).strip().upper() in ("YES", "NO", "AVUNU", "HAAN", "AAMA", "CONFIRM", "CANCEL", "సరే", "சரி"):
            if conversation.get("active_candidate_id"):
                row = service.ledger.get_candidate(
                    merchant.merchant_id, conversation["active_candidate_id"]
                )
                if row.candidate.needs_clarification or row.candidate.requires_review:
                    clarification = conversation.get("latest_question") or "Please provide the missing details first before confirming."
                    return None, OutboundMessage(
                        recipient_phone=message.sender_phone,
                        text_content=f"{clarification}\n\n(Please provide the missing details first before confirming.)",
                    )
                if (message.text_content or "").strip().upper() in ("YES", "AVUNU", "HAAN", "AAMA", "CONFIRM", "సరే", "சரி"):
                    result = service.handle_confirmation(
                        merchant, row.candidate_id, "confirm", message.message_id
                    )
                    conversation["last_candidate_id"] = row.candidate_id
                    conversation.update(
                        active_candidate_id=None,
                        latest_question=None,
                        missing_fields=[],
                        candidate_revision=None,
                        expires_at=None,
                    )
                    if isinstance(result, PostingResult):
                        return result, posting_outbound(message.sender_phone, result, language, script)
                else:
                    result = service.handle_confirmation(
                        merchant, row.candidate_id, "cancel", message.message_id
                    )
                    conversation.update(
                        active_candidate_id=None,
                        latest_question=None,
                        missing_fields=[],
                        candidate_revision=None,
                        expires_at=None,
                    )
                    labels = copy_for(language, script)
                    return None, OutboundMessage(
                        recipient_phone=message.sender_phone,
                        text_content=labels["cancelled"],
                    )
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
                return result, posting_outbound(message.sender_phone, result, language, script)
            labels = copy_for(language, script)
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=labels["cancelled"],
            )
    except ConfirmationError as exc:
        return None, OutboundMessage(
            recipient_phone=message.sender_phone, text_content=str(exc)
        )

    # Queries are read-only and intentionally do not alter pending candidate state.
    if message.message_type == MessageType.TEXT:
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
        from app.services.decision_service import DecisionService
        decision_info = DecisionService().classify_merchant_intent(
            message.text_content, has_active_candidate=True
        )
        intent = decision_info.get("intent")
        raw_msg = (message.text_content or "").strip().lower()

        # A. Query pending status (e.g. "What are pending", "status", "చూపించు", "நிலுவை")
        if intent == "query_pending_status" or raw_msg in ("continue", "pending", "status", "what are pending", "details"):
            labels = copy_for(language, script)
            summary = candidate_summary(row.candidate, row.candidate_id, language, script, review=row.candidate.requires_review)
            clarification = conversation.get("latest_question")
            body = f"{clarification}\n\n{summary}" if clarification else summary
            buttons = [CanonicalButton(id=button_id(row.candidate_id, "cancel"), title=labels["cancel"])]
            if not row.candidate.requires_review and not row.candidate.needs_clarification:
                buttons.insert(0, CanonicalButton(id=button_id(row.candidate_id, "confirm"), title=labels["confirm"]))
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                message_type=MessageType.INTERACTIVE_BUTTON,
                interactive_header="📋 " + labels.get("confirm_heading", "Pending Transaction"),
                interactive_body=body,
                interactive_footer="Clandexis Invisible ERP",
                buttons=buttons,
                text_content=body,
            )

        # B. Direct confirmation via text
        if intent == "confirm_candidate" or raw_msg in ("yes", "confirm", "ok", "sare", "avunu", "theek", "சரி", "సరే", "हाँ"):
            if not row.candidate.needs_clarification and not row.candidate.requires_review:
                result = service.handle_confirmation(merchant, row.candidate_id, "confirm", message.message_id)
                conversation["last_candidate_id"] = row.candidate_id
                conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
                if isinstance(result, PostingResult):
                    return result, posting_outbound(message.sender_phone, result, language, script)
            else:
                clarification = conversation.get("latest_question") or "Please provide the missing details first before confirming."
                return None, OutboundMessage(recipient_phone=message.sender_phone, text_content=clarification)

        # C. Direct cancellation via text
        if intent == "cancel_candidate" or raw_msg in ("cancel", "no", "vaddu", "raddu", "vendam", "వద్దు", "రద్దు", "வேண்டாம்"):
            result = service.handle_confirmation(merchant, row.candidate_id, "cancel", message.message_id)
            conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
            labels = copy_for(language, script)
            return None, OutboundMessage(recipient_phone=message.sender_phone, text_content=labels["cancelled"])

        # D. Starting a new sale while previous candidate is fully ready (not in review)
        if intent == "new_sale" and not row.candidate.needs_clarification and not row.candidate.requires_review:
            labels = copy_for(language, script)
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content=f"You have a pending transaction {row.candidate_id}. Please confirm or cancel it first.",
                message_type=MessageType.INTERACTIVE_BUTTON,
                interactive_body=f"You have an unconfirmed transaction waiting. Would you like to confirm it or cancel it?",
                buttons=[
                    CanonicalButton(id=button_id(row.candidate_id, "confirm"), title=labels["confirm"]),
                    CanonicalButton(id=button_id(row.candidate_id, "cancel"), title=labels["cancel"]),
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

    # For audio or multimodal input, resolve spoken language directly from Gemini extraction
    language, script = resolve_language_and_script(
        text=message.text_content,
        tx=tx,
        previous=conversation,
        is_audio=is_audio,
    )
    conversation.update(detected_language=language, script_preference=script)

    if tx.transaction_type in ("query", "unknown"):
        ai_reply = tx.clarification_question or (
            "This request cannot post a transaction. Use the dashboard for unsupported reports."
        )
        if "nothing posted" not in ai_reply.lower():
            ai_reply = f"{ai_reply}\n\n(Nothing posted to ledger.)"
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content=ai_reply,
        )
    if tx.validation_status == "REJECTED":
        reason = "\n".join(tx.validation_warnings)
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header="❌ Transaction Rejected",
            interactive_body=f"❌ *Transaction Rejected*\n{reason}",
            interactive_footer="Clandexis Invisible ERP",
            buttons=[
                CanonicalButton(id=f"retry_{message.message_id}", title="🔄 Try Again"),
            ],
            text_content="❌ *Transaction Rejected*\n" + reason,
        )

    try:
        if reviewing:
            if (
                tx.transaction_type != reviewing.candidate.transaction_type
                or (
                    reviewing.candidate.buyer_name
                    and tx.buyer_name
                    and reviewing.candidate.buyer_name.lower() != tx.buyer_name.lower()
                )
            ):
                raise ConfirmationError(
                    "Reply changed the buyer or transaction type. "
                    "Review the candidate in the dashboard."
                )
            if not tx.buyer_name and reviewing.candidate.buyer_name:
                tx.buyer_name = reviewing.candidate.buyer_name
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
        holds = []
        preflight = getattr(service.ledger, "confirmation_holds", None)
        if not review and preflight:
            holds = preflight(merchant.merchant_id, row.candidate_id, service.clock())
            review = bool(holds)
        all_warnings = list(holds) + list(row.candidate.validation_warnings or [])
        clarification_text, missing = question(
            row.candidate, language, script, warnings=all_warnings
        )
        if holds and not clarification_text:
            clarification_text = hold_message(holds, language, script)
        conversation.update(
            active_candidate_id=row.candidate_id,
            candidate_revision=str(row.revision),
            expires_at=row.expires_at,
            missing_fields=missing,
            latest_question=clarification_text if review else None,
        )
        summary = candidate_summary(row.candidate, row.candidate_id, language, script,
                                    review=review)
        body = f"{clarification_text}\n\n{summary}" if review else summary
        if review:
            return result, OutboundMessage(
                recipient_phone=message.sender_phone, text_content=body
            )
        labels = copy_for(language, script)
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header=labels["confirm_heading"],
            interactive_body=body,
            interactive_footer="Clandexis",
            buttons=[
                CanonicalButton(
                    id=button_id(row.candidate_id, "confirm"),
                    title=labels["confirm"],
                ),
                CanonicalButton(
                    id=button_id(row.candidate_id, "cancel"),
                    title=labels["cancel"],
                ),
            ],
        )
    except ConfirmationError as exc:
        return result, OutboundMessage(
            recipient_phone=message.sender_phone, text_content=str(exc)
        )
