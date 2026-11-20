"""
Unit tests for Meta WhatsApp Business Cloud API data models and signature verification.
"""

import hashlib
import hmac
import json
import pytest

from models.canonical import ChannelType, MessageType
from models.whatsapp_meta import MetaWebhookPayload
from webhook_service.meta_whatsapp_handler import MetaWhatsAppService
from webhook_service.security import verify_meta_signature


@pytest.fixture
def mapped_service(monkeypatch):
    monkeypatch.setattr("webhook_service.meta_whatsapp_handler.resolve_sender",
                        lambda phone: "textile-demo" if phone == "+919876543210" else None)
    return MetaWhatsAppService(token="test-token", phone_number_id="PHONE_NUM_ID_6789")


def test_verify_meta_signature():
    app_secret = "test_meta_secret_123"
    payload = b'{"object":"whatsapp_business_account","entry":[]}'
    
    # Generate valid signature
    digest = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    valid_header = f"sha256={digest}"
    
    assert verify_meta_signature(payload, valid_header, app_secret) is True
    assert verify_meta_signature(payload, "sha256=invalid_hash", app_secret) is False
    assert verify_meta_signature(payload, None, app_secret) is False


def test_meta_webhook_text_parsing(mapped_service):
    raw_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID_12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550269999",
                                "phone_number_id": "PHONE_NUM_ID_6789",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Rajesh Kumar"},
                                    "wa_id": "919876543210",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.HBgLMTIzNDU2",
                                    "timestamp": "1732080000",
                                    "type": "text",
                                    "text": {"body": "Aaj 200 cotton shirts banayi hain"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    service = mapped_service
    inbound_msgs = service.parse_webhook_payload(raw_payload)

    assert len(inbound_msgs) == 1
    msg = inbound_msgs[0]
    assert msg.message_id == "wamid.HBgLMTIzNDU2"
    assert msg.sender_phone == "+919876543210"
    assert msg.tenant_id == "textile-demo"
    value = raw_payload["entry"][0]["changes"][0]["value"]
    value["messages"][0]["from"] = "15551234567"
    assert service.parse_webhook_payload(raw_payload) == []
    value["messages"][0]["from"] = "919876543210"
    value["metadata"]["phone_number_id"] = "another-business"
    assert service.parse_webhook_payload(raw_payload) == []
    assert msg.sender_name == "Rajesh Kumar"
    assert msg.channel == ChannelType.META_WHATSAPP
    assert msg.message_type == MessageType.TEXT
    assert msg.text_content == "Aaj 200 cotton shirts banayi hain"


def test_meta_webhook_voice_note_parsing(mapped_service):
    raw_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID_12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550269999",
                                "phone_number_id": "PHONE_NUM_ID_6789",
                            },
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.VOICE_9988",
                                    "timestamp": "1732080000",
                                    "type": "audio",
                                    "audio": {
                                        "id": "MEDIA_ID_AUDIO_123",
                                        "mime_type": "audio/ogg; codecs=opus",
                                        "sha256": "abcdef123456",
                                        "voice": True,
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    service = mapped_service
    inbound_msgs = service.parse_webhook_payload(raw_payload)

    assert len(inbound_msgs) == 1
    msg = inbound_msgs[0]
    assert msg.message_id == "wamid.VOICE_9988"
    assert msg.message_type == MessageType.AUDIO
    assert msg.media_id == "MEDIA_ID_AUDIO_123"
    assert msg.media_mime_type == "audio/ogg; codecs=opus"


def test_meta_webhook_interactive_button_click(mapped_service):
    raw_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID_12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550269999",
                                "phone_number_id": "PHONE_NUM_ID_6789",
                            },
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.BTN_CLICK_777",
                                    "timestamp": "1732080000",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": "CONFIRM_SALE_TXN9823",
                                            "title": "✅ Confirm & Bill",
                                        },
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    service = mapped_service
    inbound_msgs = service.parse_webhook_payload(raw_payload)

    assert len(inbound_msgs) == 1
    msg = inbound_msgs[0]
    assert msg.message_type == MessageType.INTERACTIVE_BUTTON
    assert msg.interactive_id == "CONFIRM_SALE_TXN9823"
    assert msg.interactive_title == "✅ Confirm & Bill"


def test_meta_webhook_unregistered_sender_extraction(mapped_service):
    raw_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID_12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550269999",
                                "phone_number_id": "PHONE_NUM_ID_6789",
                            },
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.UNREG_111",
                                    "timestamp": "1732080000",
                                    "type": "text",
                                    "text": {"body": "Hello there"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    service = mapped_service
    inbound_msgs = service.parse_webhook_payload(raw_payload)
    assert inbound_msgs == []

    unregistered = service.extract_unregistered_senders(raw_payload)
    assert unregistered == ["+15551234567"]


@pytest.mark.asyncio
async def test_meta_dispatch_interactive_card(monkeypatch):
    from models.canonical import OutboundMessage, CanonicalButton
    
    service = MetaWhatsAppService(token="fake_token", phone_number_id="123456")
    captured_payload = None

    async def mock_post(payload):
        nonlocal captured_payload
        captured_payload = payload
        return None

    monkeypatch.setattr(service, "_post_message", mock_post)

    outbound = OutboundMessage(
        recipient_phone="+919876543210",
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_header="⚠️ Confirmation Required",
        interactive_body="Party: Ramesh\nTotal: ₹5000.00\nUdhaar: ₹3000.00",
        interactive_footer="Clandexis Invisible ERP",
        buttons=[
            CanonicalButton(id="CONFIRM_123", title="✅ Confirm (हाँ)"),
            CanonicalButton(id="CANCEL_123", title="❌ Cancel (रद्द)"),
        ],
    )

    await service.dispatch_outbound(outbound)
    assert captured_payload is not None
    assert captured_payload.to == "919876543210"
    assert captured_payload.interactive.header.text == "⚠️ Confirmation Required"
    assert "Party: Ramesh" in captured_payload.interactive.body.text
    assert len(captured_payload.interactive.action.buttons) == 2
    assert captured_payload.interactive.action.buttons[0].reply.id == "CONFIRM_123"
