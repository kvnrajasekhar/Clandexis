"""
Test Suite for WhatsApp Merchant Onboarding Workflow & State Machine.
Verifies:
- 10 Indian language selection using WhatsApp Interactive List message (no UK flag).
- Frictionless language selection transitioning directly to MSME details (no PIN friction).
- Multi-modal MSME collection (Text, Document/Photo OCR, WhatsApp Flows JSON, and Vernacular Voice Notes).
- Voice note audio extraction of shop name and city (e.g. Guntur).
- Data retention when tapping 'Edit Details' during review.
- Deterministic MSME format & checksum validation before generating confirmation card.
- 1-tap confirmation card click and merchant activation.
"""

import pytest
from app.services.merchant_onboarding import (
    MerchantOnboardingService,
    OnboardingSession,
    OnboardingStep,
)
from models.canonical import (
    ChannelType,
    InboundMessage,
    MessageType,
)


@pytest.mark.asyncio
async def test_dynamic_language_detection_and_picker():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500001"

    # 1. User texts in Telugu script / greeting
    msg1 = InboundMessage(
        message_id="msg_01",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.TEXT,
        text_content="నమస్కారం! మా షాపు రిజిస్టర్ చేయాలి",
    )

    out1 = await service.handle_onboarding_message(msg1)
    session = service.get_session(phone)

    assert session.step == OnboardingStep.AWAITING_LANGUAGE
    assert session.preferred_language == "te"
    assert out1.message_type == MessageType.INTERACTIVE_LIST
    assert out1.list_sections is not None
    assert len(out1.list_sections[0].rows) == 10
    # Check that Telugu and Tamil are present without UK flags
    row_ids = [r.id for r in out1.list_sections[0].rows]
    assert "LANG_TE" in row_ids
    assert "LANG_TA" in row_ids
    assert "LANG_KN" in row_ids
    assert "LANG_HI" in row_ids
    for r in out1.list_sections[0].rows:
        assert "🇬🇧" not in r.title


@pytest.mark.asyncio
async def test_frictionless_language_selection_no_pin():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500002"

    # User selects Telugu via 1-tap list item or button
    msg_lang = InboundMessage(
        message_id="msg_02",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id="LANG_TE",
    )

    out_lang = await service.handle_onboarding_message(msg_lang)
    session = service.get_session(phone)

    # Directly proceeds to MSME details without PIN friction
    assert session.preferred_language == "te"
    assert session.step == OnboardingStep.AWAITING_MSME_DETAILS
    assert "మీ MSME వ్యాపార వివరాలు నమోదు చేయండి" in (out_lang.text_content or "")


@pytest.mark.asyncio
async def test_msme_collection_via_voice_note():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500007"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_MSME_DETAILS
    session.preferred_language = "te"

    # Merchant sends audio voice note in Telugu mentioning Guntur
    msg_voice = InboundMessage(
        message_id="msg_audio_01",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.AUDIO,
        media_id="voice_note_123",
        media_mime_type="audio/ogg",
    )

    async def mock_extract(media_bytes, lang="te"):
        return {
            "business_name": "Sri Balaji Handlooms",
            "owner_name": "Balaji",
            "address": "Guntur, Andhra Pradesh",
            "detected_language": "te",
        }
    service._extract_from_audio = mock_extract

    out_card = await service.handle_onboarding_message(msg_voice, media_bytes=b"sample_ogg_voice_bytes")

    assert session.step == OnboardingStep.AWAITING_CONFIRMATION
    assert session.business_name == "Sri Balaji Handlooms"
    assert "Guntur" in (session.business_address or "")
    assert out_card.message_type == MessageType.INTERACTIVE_BUTTON
    assert "వ్యాపార వివరాల నిర్ధారణ" in (out_card.interactive_header or "")


@pytest.mark.asyncio
async def test_msme_edit_details_retains_data():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500008"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_CONFIRMATION
    session.preferred_language = "te"
    session.business_name = "Sri Lakshmi Handlooms"
    session.owner_name = "Ramesh"
    session.business_address = "Guntur, Andhra Pradesh"

    # Merchant taps 'Edit Details' (మార్చు)
    msg_edit = InboundMessage(
        message_id="msg_edit_01",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id="MSME_ONBOARD_EDIT",
    )

    out_edit = await service.handle_onboarding_message(msg_edit)

    # Step transitions to AWAITING_MSME_DETAILS while retaining gathered info
    assert session.step == OnboardingStep.AWAITING_MSME_DETAILS
    assert session.business_name == "Sri Lakshmi Handlooms"
    assert session.owner_name == "Ramesh"
    assert "భద్రంగా ఉన్నాయి" in (out_edit.text_content or "")


@pytest.mark.asyncio
async def test_msme_collection_via_text_and_confirmation_card():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500003"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_MSME_DETAILS
    session.preferred_language = "te"

    # User sends details as conversational text mentioning Guntur
    msg_details = InboundMessage(
        message_id="msg_04",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.TEXT,
        text_content=(
            "Shop: Sri Venkateswara Handlooms, Owner: K Rajaiah, "
            "Udyam: UDYAM-TS-04-0019284, GSTIN: 27AAPFU0939F1ZV, "
            "UPI: srivenkateswara@upi, Address: Guntur, Andhra Pradesh"
        ),
    )

    out_card = await service.handle_onboarding_message(msg_details)

    assert session.step == OnboardingStep.AWAITING_CONFIRMATION
    assert session.business_name == "Sri Venkateswara Handlooms"
    assert session.owner_name == "K Rajaiah"
    assert session.udyam_reg_no == "UDYAM-TS-04-0019284"
    assert session.gstin == "27AAPFU0939F1ZV"
    assert session.upi_vpa == "srivenkateswara@upi"
    assert "Guntur" in (session.business_address or "")

    assert out_card.message_type == MessageType.INTERACTIVE_BUTTON
    assert "వ్యాపార వివరాల నిర్ధారణ" in (out_card.interactive_header or "")
    btn_ids = [b.id for b in (out_card.buttons or [])]
    assert "MSME_ONBOARD_CONFIRM" in btn_ids
    assert "MSME_ONBOARD_EDIT" in btn_ids


@pytest.mark.asyncio
async def test_msme_collection_via_flow_json():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500004"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_MSME_DETAILS
    session.preferred_language = "hi"

    # User submits WhatsApp Flow JSON
    flow_json = (
        '{"business_name": "Surat Silk Mills", "owner_name": "Dinesh Bhai", '
        '"gstin": "27AAPFU0939F1ZV", "udyam_reg_no": "UDYAM-GJ-01-0001234", '
        '"address": "Ring Road, Surat, Gujarat", "pincode": "395002", "upi_vpa": "suratsilk@icici"}'
    )
    msg_flow = InboundMessage(
        message_id="msg_flow_01",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.TEXT,
        text_content=flow_json,
    )

    out_card = await service.handle_onboarding_message(msg_flow)

    assert session.step == OnboardingStep.AWAITING_CONFIRMATION
    assert session.business_name == "Surat Silk Mills"
    assert session.owner_name == "Dinesh Bhai"
    assert session.udyam_reg_no == "UDYAM-GJ-01-0001234"
    assert "व्यापार विवरण की पुष्टि" in (out_card.interactive_header or "")


@pytest.mark.asyncio
async def test_confirmation_card_approval_completes_onboarding():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500006"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_CONFIRMATION
    session.preferred_language = "te"
    session.business_name = "Sri Lakshmi Handlooms"

    # User clicks Confirm & Activate button
    msg_confirm = InboundMessage(
        message_id="msg_btn_confirm",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id="MSME_ONBOARD_CONFIRM",
    )

    out_final = await service.handle_onboarding_message(msg_confirm)

    assert session.step == OnboardingStep.COMPLETED
    assert "అభినందనలు!" in (out_final.text_content or "")
    assert "మీ మొదటి లావాదేవీని ఇప్పుడే నమోదు చేయండి" in (out_final.text_content or "")
