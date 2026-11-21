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
        raw_msg = (message.text_content or "").strip().lower()
        from app.services.decision_service import DecisionService
        decision_info = DecisionService().classify_merchant_intent(
            message.text_content, has_active_candidate=bool(conversation.get("active_candidate_id"))
        )
        intent = decision_info.get("intent")

        # 1. Interactive Button reply handling
        reply = service.parse_reply(merchant, message)
        if reply:
            candidate_id, action = reply
            result = service.handle_confirmation(
                merchant, candidate_id, action, message.message_id
            )
            conversation["last_candidate_id"] = candidate_id
            active_list = service.ledger.active_candidates(merchant.merchant_id, service.clock())
            rem = [c for c in active_list if c.candidate_id != candidate_id]
            if rem:
                conversation.update(
                    active_candidate_id=rem[-1].candidate_id,
                    candidate_revision=str(rem[-1].revision),
                    expires_at=rem[-1].expires_at,
                )
            else:
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

        # 2. Text Cancellation handling (e.g. "Cancel the previous transaction", "cancel all", "రద్దు")
        if message.message_type == MessageType.TEXT and (
            intent == "cancel_candidate"
            or re.search(r"\b(cancel|raddu|vaddu|drop|discard|delete)\b|^[❌🚫]|వద్దు|రద్దు|వేండாம்|नहीं", raw_msg, re.I)
        ):
            active_list = service.ledger.active_candidates(merchant.merchant_id, service.clock())
            if active_list:
                labels = copy_for(language, script)
                if "all" in raw_msg:
                    for ac in active_list:
                        service.handle_confirmation(merchant, ac.candidate_id, "cancel", f"{message.message_id}_{ac.candidate_id[:6]}")
                    conversation.update(
                        active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None
                    )
                    return None, OutboundMessage(
                        recipient_phone=message.sender_phone,
                        text_content=f"✅ Cancelled all ({len(active_list)}) pending transactions. Nothing posted to ledger.",
                    )
                else:
                    target_cid = conversation.get("active_candidate_id")
                    target = next((c for c in active_list if c.candidate_id == target_cid), active_list[-1])
                    result = service.handle_confirmation(merchant, target.candidate_id, "cancel", message.message_id)
                    rem = [c for c in active_list if c.candidate_id != target.candidate_id]
                    if rem:
                        conversation.update(
                            active_candidate_id=rem[-1].candidate_id,
                            candidate_revision=str(rem[-1].revision),
                            expires_at=rem[-1].expires_at,
                        )
                    else:
                        conversation.update(
                            active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None
                        )
                    t_desc = target.candidate.buyer_name or (target.candidate.items[0].item_name if target.candidate.items else "Transaction")
                    amt_str = f": ₹{target.candidate.total_amount:,.2f}" if target.candidate.total_amount is not None else ""
                    reply_text = f"✅ Cancelled previous transaction ({t_desc}{amt_str}). Nothing was posted to your ledger."
                    if rem:
                        reply_text += f"\n\nYou still have {len(rem)} other pending transaction(s) on hold."
                    return None, OutboundMessage(
                        recipient_phone=message.sender_phone,
                        text_content=reply_text,
                    )
            else:
                return None, OutboundMessage(
                    recipient_phone=message.sender_phone,
                    text_content="There are no active pending transactions to cancel.",
                )

        # 3. Text Confirmation handling (e.g. "Confirm all", "Confirm", "YES", "సరే")
        if message.message_type == MessageType.TEXT and (
            intent == "confirm_candidate"
            or raw_msg in ("yes", "confirm", "ok", "sare", "avunu", "theek", "சரி", "సరే", "हाँ")
            or re.search(r"\bconfirm\s+all\b", raw_msg, re.I)
        ):
            active_list = service.ledger.active_candidates(merchant.merchant_id, service.clock())
            if not active_list:
                return None, OutboundMessage(
                    recipient_phone=message.sender_phone,
                    text_content="There are no active pending transactions to confirm.",
                )
            if "all" in raw_msg:
                preflight = getattr(service.ledger, "confirmation_holds", None)
                ready_cands = []
                for c in active_list:
                    c_holds = preflight(merchant.merchant_id, c.candidate_id, service.clock()) if preflight else []
                    if not c.candidate.needs_clarification and not c_holds and not c.candidate.missing_fields:
                        ready_cands.append(c)
                if not ready_cands:
                    clarification = conversation.get("latest_question") or "Pending transactions require missing details or review before they can be confirmed."
                    return None, OutboundMessage(recipient_phone=message.sender_phone, text_content=clarification)
                results = []
                for rc in ready_cands:
                    try:
                        res = service.handle_confirmation(merchant, rc.candidate_id, "confirm", f"{message.message_id}_{rc.candidate_id[:6]}")
                        if isinstance(res, PostingResult):
                            results.append(res)
                    except ConfirmationError:
                        pass
                rem = [c for c in active_list if c not in ready_cands]
                if rem:
                    conversation.update(active_candidate_id=rem[-1].candidate_id)
                else:
                    conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
                lines = [f"✅ *Recorded {len(results)} Transactions to Ledger:*\n━━━━━━━━━━━━━━━━━━━"]
                for r in results:
                    tot = (r.amount_paid or Decimal(0)) + (r.credit_outstanding or Decimal(0))
                    desc = f"₹{tot:,.2f}" if tot > 0 else "Stock In"
                    lines.append(f"• Txn #{r.transaction_id}: {desc} ({r.posting_status})")
                lines.append("━━━━━━━━━━━━━━━━━━━")
                if rem:
                    lines.append(f"⚠️ {len(rem)} transaction(s) still need details before confirming.")
                return (results[-1] if results else None), OutboundMessage(recipient_phone=message.sender_phone, text_content="\n".join(lines))
            else:
                target_cid = conversation.get("active_candidate_id")
                target = next((c for c in active_list if c.candidate_id == target_cid), active_list[-1])
                preflight = getattr(service.ledger, "confirmation_holds", None)
                holds = preflight(merchant.merchant_id, target.candidate_id, service.clock()) if preflight else []
                if (target.candidate.needs_clarification or target.candidate.requires_review) and bool(holds):
                    clarification = hold_message(holds, language, script) if holds else (conversation.get("latest_question") or "Please provide the missing details first before confirming.")
                    return None, OutboundMessage(
                        recipient_phone=message.sender_phone,
                        text_content=f"{clarification}\n\n(Please provide the missing details first before confirming.)",
                    )
                result = service.handle_confirmation(merchant, target.candidate_id, "confirm", message.message_id)
                conversation["last_candidate_id"] = target.candidate_id
                rem = [c for c in active_list if c.candidate_id != target.candidate_id]
                if rem:
                    conversation.update(
                        active_candidate_id=rem[-1].candidate_id,
                        candidate_revision=str(rem[-1].revision),
                        expires_at=rem[-1].expires_at,
                    )
                else:
                    conversation.update(active_candidate_id=None, latest_question=None, missing_fields=[], candidate_revision=None, expires_at=None)
                if isinstance(result, PostingResult):
                    return result, posting_outbound(message.sender_phone, result, language, script)
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

    is_inventory_msg = bool(
        intent == "stock_added"
        or re.search(
            r"\b(inventory|stock|maal|saraku|boxes|bottles|packets|pieces)\b.*\b(have|add|added|in my|opening)\b|\b(have|got|added|opening)\b.*\b(inventory|stock|boxes|packets)\b|స్టాక్|సరుకు|ఇన్వెంటరీ",
            raw_msg,
            re.I,
        )
    )

    # Status queries for pending candidates
    if not is_inventory_msg and message.message_type == MessageType.TEXT and (
        intent == "query_pending_status"
        or raw_msg in ("continue", "pending", "status", "what are pending", "details")
    ):
        active_list = service.ledger.active_candidates(merchant.merchant_id, service.clock())
        labels = copy_for(language, script)
        if active_list:
            lines = [f"📋 *Pending Transactions on Hold ({len(active_list)}):*\n━━━━━━━━━━━━━━━━━━━"]
            for i, ac in enumerate(active_list, start=1):
                b_name = ac.candidate.buyer_name or ("Stock In" if getattr(ac.candidate, "transaction_type", "") in ("stock_added", "production") else "Sale")
                st = "Needs Review" if (ac.candidate.requires_review or ac.candidate.needs_clarification) else "Ready to Confirm"
                amt_str = f": ₹{ac.candidate.total_amount:,.2f}" if ac.candidate.total_amount is not None else ""
                lines.append(f"{i}. *{b_name}*{amt_str} — _{st}_ (ID: {ac.candidate_id[:8]}...)")
            lines.append("━━━━━━━━━━━━━━━━━━━")
            lines.append("These transactions are on hold and will NOT be committed until you confirm.")
            lines.append("Reply *Confirm* or *Confirm all* to post, or *Cancel previous* to discard.")
            body = "\n".join(lines)
            buttons = []
            latest = active_list[-1]
            if not latest.candidate.requires_review and not latest.candidate.needs_clarification:
                buttons.append(CanonicalButton(id=button_id(latest.candidate_id, "confirm"), title=labels["confirm"]))
            buttons.append(CanonicalButton(id=button_id(latest.candidate_id, "cancel"), title=labels["cancel"]))
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                message_type=MessageType.INTERACTIVE_BUTTON,
                interactive_header="📋 " + labels.get("confirm_heading", "Pending Transactions"),
                interactive_body=body,
                interactive_footer="Clandexis Invisible ERP",
                buttons=buttons,
                text_content=body,
            )
        else:
            return None, OutboundMessage(
                recipient_phone=message.sender_phone,
                text_content="✅ No transactions are currently pending.",
            )

    reviewing = None
    is_media_msg = (
        message.message_type in (MessageType.IMAGE, MessageType.AUDIO, MessageType.DOCUMENT)
        or media_bytes is not None
        or bool(message.media_url)
    )
    if (
        not is_media_msg
        and not is_inventory_msg
        and message.message_type == MessageType.TEXT
        and conversation.get("active_candidate_id")
        and (
            intent == "clarify_field"
            or re.search(r"^\d+(\.\d+)?$|^price\b|^rate\b|^cost\b|ధర|ఖరీదు|விலை|भाव", raw_msg, re.I)
        )
    ):
        try:
            row = service.ledger.get_candidate(
                merchant.merchant_id, conversation["active_candidate_id"]
            )
            if row.status == "AWAITING_CONFIRMATION" and row.expires_at > service.clock():
                if row.candidate.needs_clarification or row.candidate.requires_review:
                    reviewing = row
        except Exception:
            pass

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
    if len(transactions) > 1:
        staged_rows = []
        for idx, t_item in enumerate(transactions):
            r = service.stage(merchant, message.message_id, idx, t_item)
            staged_rows.append(r)
        last_row = staged_rows[-1]
        conversation.update(
            active_candidate_id=last_row.candidate_id,
            candidate_revision=str(last_row.revision),
            expires_at=last_row.expires_at,
        )
        lines = [f"📋 *{len(staged_rows)} Transactions Extracted & Staged:*\n━━━━━━━━━━━━━━━━━━━"]
        for i, sr in enumerate(staged_rows, start=1):
            b = sr.candidate.buyer_name or ("Stock In" if getattr(sr.candidate, "transaction_type", "") in ("stock_added", "production") else "Sale")
            amt_str = f": ₹{sr.candidate.total_amount:,.2f}" if sr.candidate.total_amount is not None else ""
            lines.append(f"{i}. *{b}*{amt_str} (Candidate: {sr.candidate_id[:8]}...)")
        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append("Nothing posted yet. All transactions are held pending your confirmation.")
        lines.append("Reply *Confirm all* to post all, or *Cancel all* to discard.")
        return result, OutboundMessage(
            recipient_phone=message.sender_phone,
            text_content="\n".join(lines),
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
            is_same = (
                tx.transaction_type == reviewing.candidate.transaction_type
                and (
                    not reviewing.candidate.buyer_name
                    or not tx.buyer_name
                    or reviewing.candidate.buyer_name.lower() == tx.buyer_name.lower()
                )
                and (
                    len(tx.items) == len(reviewing.candidate.items)
                    if (reviewing.candidate.items and tx.items)
                    else True
                )
            )
            if is_same:
                if not tx.buyer_name and reviewing.candidate.buyer_name:
                    tx.buyer_name = reviewing.candidate.buyer_name
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
        active_list = service.ledger.active_candidates(merchant.merchant_id, service.clock())
        if len(active_list) > 1:
            queue_info = f"\n\n📋 *All Pending Transactions ({len(active_list)} on hold)*:\n"
            for i, ac in enumerate(active_list, start=1):
                b = ac.candidate.buyer_name or ("Stock In" if getattr(ac.candidate, "transaction_type", "") in ("stock_added", "production") else "Sale")
                cur = " 👈 (Latest)" if ac.candidate_id == row.candidate_id else ""
                amt_str = f": ₹{ac.candidate.total_amount:,.2f}" if ac.candidate.total_amount is not None else ""
                queue_info += f"• #{i} {b}{amt_str}{cur}\n"
            queue_info += "\n(Held on hold until confirmed. Reply 'Confirm all' to post all, or 'Cancel previous'.)"
            body += queue_info
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
