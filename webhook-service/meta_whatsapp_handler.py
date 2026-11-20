"""
Meta WhatsApp Business Cloud API Client & Webhook Ingestion Handler.
Handles Webhook verification, payload normalization into InboundMessage,
media downloading (voice notes & kacha bills), and outbound message dispatching.
"""

import logging
import os
from typing import Any, Dict, List, Optional
import httpx

from config import settings
from models.canonical import CanonicalButton, ChannelType, InboundMessage, MessageType, OutboundMessage
from models.whatsapp_meta import (
    MetaInteractiveAction,
    MetaInteractiveBody,
    MetaInteractiveButton,
    MetaInteractiveButtonReply,
    MetaInteractiveFooter,
    MetaInteractiveHeader,
    MetaInteractivePayload,
    MetaMediaPayload,
    MetaMessage,
    MetaMessageType,
    MetaSendMessageRequest,
    MetaSendResponse,
    MetaTextPayload,
    MetaWebhookPayload,
)

logger = logging.getLogger("meta-whatsapp-handler")

class MetaWhatsAppService:
    def __init__(
        self,
        token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        self.token = token or settings.META_WHATSAPP_TOKEN or ""
        self.phone_number_id = phone_number_id or settings.META_PHONE_NUMBER_ID or ""
        self.app_secret = app_secret or settings.META_APP_SECRET or ""
        self.api_version = settings.META_API_VERSION
        self.graph_base_url = settings.META_GRAPH_BASE_URL
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.http_client.aclose()

    # ==========================================================================
    # Webhook Parsing & Normalization
    # ==========================================================================

    def parse_webhook_payload(self, raw_data: Dict[str, Any]) -> List[InboundMessage]:
        """
        Parses raw webhook JSON payload into a list of normalized canonical InboundMessages.
        """
        inbound_messages: List[InboundMessage] = []
        try:
            payload = MetaWebhookPayload.model_validate(raw_data)
        except Exception as e:
            logger.error("Invalid Meta webhook schema")
            return inbound_messages

        for entry in payload.entry:
            for change in entry.changes:
                value = change.value
                if not value.messages:
                    # Ignore status delivery receipts or empty message batches
                    continue

                contact_map = {c.wa_id: (c.profile.name if c.profile else None) for c in value.contacts or []}

                for msg in value.messages:
                    sender_wa_id = msg.from_user
                    sender_phone = f"+{sender_wa_id}" if not sender_wa_id.startswith("+") else sender_wa_id
                    sender_name = contact_map.get(sender_wa_id)
                    tenant_id = sender_phone  # By default, MSME merchant is identified by E.164 phone

                    canonical_msg = self._normalize_message(
                        msg=msg,
                        tenant_id=tenant_id,
                        sender_phone=sender_phone,
                        sender_name=sender_name,
                        raw_payload=raw_data,
                    )
                    if canonical_msg:
                        inbound_messages.append(canonical_msg)

        return inbound_messages

    def _normalize_message(
        self,
        msg: MetaMessage,
        tenant_id: str,
        sender_phone: str,
        sender_name: Optional[str],
        raw_payload: Dict[str, Any],
    ) -> Optional[InboundMessage]:
        """
        Converts single MetaMessage into normalized InboundMessage.
        """
        msg_type = MessageType.UNKNOWN
        text_content = None
        media_id = None
        media_mime_type = None
        interactive_id = None
        interactive_title = None

        if msg.type == MetaMessageType.TEXT and msg.text:
            msg_type = MessageType.TEXT
            text_content = msg.text.body

        elif msg.type in (MetaMessageType.AUDIO, MetaMessageType.VOICE) and (msg.audio or msg.voice):
            msg_type = MessageType.AUDIO
            media_obj = msg.voice or msg.audio
            media_id = media_obj.id
            media_mime_type = media_obj.mime_type

        elif msg.type == MetaMessageType.IMAGE and msg.image:
            msg_type = MessageType.IMAGE
            media_id = msg.image.id
            media_mime_type = msg.image.mime_type
            text_content = msg.image.caption

        elif msg.type == MetaMessageType.DOCUMENT and msg.document:
            msg_type = MessageType.DOCUMENT
            media_id = msg.document.id
            media_mime_type = msg.document.mime_type
            text_content = msg.document.caption

        elif msg.type == MetaMessageType.INTERACTIVE and msg.interactive:
            interactive = msg.interactive
            if interactive.button_reply:
                msg_type = MessageType.INTERACTIVE_BUTTON
                interactive_id = interactive.button_reply.id
                interactive_title = interactive.button_reply.title
                text_content = interactive.button_reply.title
            elif interactive.list_reply:
                msg_type = MessageType.INTERACTIVE_LIST
                interactive_id = interactive.list_reply.id
                interactive_title = interactive.list_reply.title
                text_content = interactive.list_reply.title

        return InboundMessage(
            message_id=msg.id,
            tenant_id=tenant_id,
            sender_phone=sender_phone,
            sender_name=sender_name,
            channel=ChannelType.META_WHATSAPP,
            message_type=msg_type,
            text_content=text_content,
            media_id=media_id,
            media_mime_type=media_mime_type,
            interactive_id=interactive_id,
            interactive_title=interactive_title,
            raw_payload=raw_payload,
        )

    # ==========================================================================
    # Media Retrieval
    # ==========================================================================

    async def get_media_url(self, media_id: str) -> Optional[str]:
        """
        Queries Meta Graph API to resolve the temporary download URL for a media asset.
        """
        endpoint = f"{self.graph_base_url}/{self.api_version}/{media_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            resp = await self.http_client.get(endpoint, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("url")
        except Exception as e:
            logger.error("Media URL retrieval failed (%s)", type(e).__name__)
            return None

    async def download_media_bytes(self, media_id: str) -> Optional[bytes]:
        """
        Downloads binary media content (e.g. .ogg voice notes or .jpg bills) from Meta CDN.
        """
        download_url = await self.get_media_url(media_id)
        if not download_url:
            return None

        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            resp = await self.http_client.get(download_url, headers=headers)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error("Media download failed (%s)", type(e).__name__)
            return None

    # ==========================================================================
    # Outbound Dispatching
    # ==========================================================================

    async def dispatch_outbound(self, message: OutboundMessage) -> Optional[MetaSendResponse]:
        """
        Dispatches a canonical OutboundMessage through Meta WhatsApp Cloud API.
        """
        to_phone = message.recipient_phone.replace("+", "")
        
        if message.message_type == MessageType.TEXT and message.text_content:
            return await self.send_text_message(to_phone, message.text_content)
        
        elif message.message_type == MessageType.INTERACTIVE_BUTTON and message.buttons:
            return await self.send_interactive_buttons(
                to=to_phone,
                body_text=message.interactive_body or message.text_content or "Please confirm:",
                buttons=message.buttons,
                header_text=message.interactive_header,
                footer_text=message.interactive_footer,
            )
            
        elif message.message_type == MessageType.DOCUMENT and message.document_url:
            return await self.send_document_message(
                to=to_phone,
                document_url=message.document_url,
                filename=message.document_filename or "invoice.pdf",
                caption=message.text_content,
            )
            
        elif message.message_type == MessageType.AUDIO and message.audio_url:
            return await self.send_audio_message(
                to=to_phone,
                audio_url=message.audio_url,
            )

        logger.warning(f"Unsupported outbound message format: {message.message_type}")
        return None

    async def send_text_message(self, to: str, text: str) -> Optional[MetaSendResponse]:
        """
        Sends formatted text message.
        """
        payload = MetaSendMessageRequest(
            messaging_product="whatsapp",
            recipient_type="individual",
            to=to.replace("+", ""),
            type=MetaMessageType.TEXT,
            text=MetaTextPayload(body=text),
        )
        return await self._post_message(payload)

    async def send_interactive_buttons(
        self,
        to: str,
        body_text: str,
        buttons: List[CanonicalButton],
        header_text: Optional[str] = None,
        footer_text: Optional[str] = "Clandexis Invisible ERP",
    ) -> Optional[MetaSendResponse]:
        """
        Sends 1-tap interactive confirmation buttons (up to 3 buttons supported by Meta API).
        Essential for the <85% confidence and udhaar safety gate!
        """
        meta_buttons = [
            MetaInteractiveButton(
                type="reply",
                reply=MetaInteractiveButtonReply(id=btn.id, title=btn.title[:20]),
            )
            for btn in buttons[:3]
        ]

        payload = MetaSendMessageRequest(
            messaging_product="whatsapp",
            recipient_type="individual",
            to=to.replace("+", ""),
            type=MetaMessageType.INTERACTIVE,
            interactive=MetaInteractivePayload(
                type="button",
                header=MetaInteractiveHeader(type="text", text=header_text) if header_text else None,
                body=MetaInteractiveBody(text=body_text),
                footer=MetaInteractiveFooter(text=footer_text) if footer_text else None,
                action=MetaInteractiveAction(buttons=meta_buttons),
            ),
        )
        return await self._post_message(payload)

    async def send_document_message(
        self,
        to: str,
        document_url: str,
        filename: str = "invoice.pdf",
        caption: Optional[str] = None,
    ) -> Optional[MetaSendResponse]:
        """
        Sends PDF invoices or cryptographically attested credit reports.
        """
        payload = MetaSendMessageRequest(
            messaging_product="whatsapp",
            recipient_type="individual",
            to=to.replace("+", ""),
            type=MetaMessageType.DOCUMENT,
            document=MetaMediaPayload(
                link=document_url,
                filename=filename,
                caption=caption,
            ),
        )
        return await self._post_message(payload)

    async def send_audio_message(self, to: str, audio_url: str) -> Optional[MetaSendResponse]:
        """
        Sends Gemini TTS synthesized voice readback.
        """
        payload = MetaSendMessageRequest(
            messaging_product="whatsapp",
            recipient_type="individual",
            to=to.replace("+", ""),
            type=MetaMessageType.AUDIO,
            audio=MetaMediaPayload(link=audio_url),
        )
        return await self._post_message(payload)

    async def mark_message_as_read(self, message_id: str) -> bool:
        """
        Sends read status receipt to turn tick marks blue on WhatsApp.
        """
        endpoint = f"{self.graph_base_url}/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            resp = await self.http_client.post(endpoint, json=body, headers=headers)
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Read receipt failed (%s)", type(e).__name__)
            return False

    async def _post_message(self, payload: MetaSendMessageRequest) -> Optional[MetaSendResponse]:
        """
        Internal POST call to Meta Graph API messages endpoint.
        """
        endpoint = f"{self.graph_base_url}/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self.http_client.post(
                endpoint,
                content=payload.model_dump_json(exclude_none=True, by_alias=True),
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.error("Meta Graph API error (HTTP %s)", resp.status_code)
                return None
            return MetaSendResponse.model_validate(resp.json())
        except Exception as e:
            logger.error("Meta dispatch failed (%s)", type(e).__name__)
            return None
