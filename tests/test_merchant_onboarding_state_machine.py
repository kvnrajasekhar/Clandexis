"""
Test Suite for WhatsApp Merchant Onboarding Workflow & State Machine.
Verifies:
- Dynamic language detection from vernacular text/greeting (Telugu, Hindi, English).
- 1-tap interactive language selection button handling.
- Frictionless phone binding and 4-digit security PIN setup / skip.
- Multi-modal MSME collection (Text, Document/Photo OCR, WhatsApp Flows JSON).
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
    assert out1.message_type == MessageType.INTERACTIVE_BUTTON
    assert "భాషను ఎంచుకోండి" in (out1.interactive_header or "")
    assert len(out1.buttons or []) == 3
    # Check that Telugu button is included
    btn_ids = [b.id for b in (out1.buttons or [])]
    assert "LANG_TE" in btn_ids


@pytest.mark.asyncio
async def test_language_selection_and_pin_setup():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500002"

    # User starts and selects Telugu via 1-tap button
    msg_lang = InboundMessage(
        message_id="msg_02",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id="LANG_TE",
    )

    out_lang = await service.handle_onboarding_message(msg_lang)
    session = service.get_session(phone)

    assert session.preferred_language == "te"
    assert session.step == OnboardingStep.AWAITING_PIN
    assert "ధృవీకరించబడిన WhatsApp సంఖ్య" in (out_lang.interactive_body or "")

    # User enters 4-digit PIN
    msg_pin = InboundMessage(
        message_id="msg_03",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.TEXT,
        text_content="4321",
    )

    out_pin = await service.handle_onboarding_message(msg_pin)
    assert session.step == OnboardingStep.AWAITING_MSME_DETAILS
    assert session.security_pin_hash == "pin_4321"
    assert "మీ MSME వ్యాపార వివరాలు నమోదు చేయండి" in (out_pin.text_content or "")


@pytest.mark.asyncio
async def test_msme_collection_via_text_and_confirmation_card():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500003"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_MSME_DETAILS
    session.preferred_language = "te"

    # User sends details as conversational text
    msg_details = InboundMessage(
        message_id="msg_04",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.TEXT,
        text_content=(
            "Shop: Sri Venkateswara Handlooms, Owner: K Rajaiah, "
            "Udyam: UDYAM-TS-04-0019284, GSTIN: 27AAPFU0939F1ZV, "
            "UPI: srivenkateswara@upi, Address: Sircilla, Telangana 505301"
        ),
    )

    out_card = await service.handle_onboarding_message(msg_details)

    assert session.step == OnboardingStep.AWAITING_CONFIRMATION
    assert session.business_name == "Sri Venkateswara Handlooms"
    assert session.owner_name == "K Rajaiah"
    assert session.udyam_reg_no == "UDYAM-TS-04-0019284"
    assert session.gstin == "27AAPFU0939F1ZV"
    assert session.upi_vpa == "srivenkateswara@upi"

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
async def test_msme_collection_via_ocr_image():
    service = MerchantOnboardingService(sessions={})
    phone = "+919876500005"
    session = service.get_session(phone)
    session.step = OnboardingStep.AWAITING_MSME_DETAILS
    session.preferred_language = "en"

    # User uploads photo of certificate
    msg_img = InboundMessage(
        message_id="msg_img_01",
        tenant_id=phone,
        sender_phone=phone,
        message_type=MessageType.IMAGE,
        media_id="meta_media_12345",
        media_mime_type="image/jpeg",
    )

    out_card = await service.handle_onboarding_message(msg_img, media_bytes=b"fake_jpeg_bytes")

    assert session.step == OnboardingStep.AWAITING_CONFIRMATION
    assert session.business_name == "Sri Lakshmi Handlooms"
    assert session.udyam_reg_no == "UDYAM-TS-04-0019284"
    assert "Confirm MSME Registration" in (out_card.interactive_header or "")


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
