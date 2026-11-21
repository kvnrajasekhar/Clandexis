"""
Canonical Channel Adapter Models.
Decouples channel-specific protocols (Meta WhatsApp Cloud API, Twilio, Web Simulator)
from core AI reasoning, human-in-the-loop safety gates, and atomic ledger mutations.
(FR-06 & FR-07 Omnichannel Plug-and-Play Specification).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class ChannelType(str, Enum):
    META_WHATSAPP = "meta_whatsapp"
    TWILIO_WHATSAPP = "twilio_whatsapp"
    WEB_SIMULATOR = "web_simulator"
    TELEGRAM = "telegram"
    RCS = "rcs"


class MessageType(str, Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    INTERACTIVE_BUTTON = "interactive_button"
    INTERACTIVE_LIST = "interactive_list"
    STATUS_RECEIPT = "status_receipt"
    UNKNOWN = "unknown"


class CanonicalButton(BaseModel):
    id: str = Field(..., description="Candidate-specific canonical action identifier")
    title: str = Field(..., max_length=20, description="Short user-facing button label, e.g. 'Confirm (हाँ)'")


class CanonicalListRow(BaseModel):
    id: str = Field(..., max_length=200, description="Unique row identifier")
    title: str = Field(..., max_length=24, description="Title of the row")
    description: Optional[str] = Field(None, max_length=72, description="Optional description of the row")


class CanonicalListSection(BaseModel):
    title: Optional[str] = Field(None, max_length=24, description="Section heading")
    rows: List[CanonicalListRow] = Field(default_factory=list, description="Rows under this section")


class InboundMessage(BaseModel):
    """
    Normalized inbound message contract.
    All channel adapters (Meta WhatsApp, Twilio, etc.) map incoming payloads into this.
    """
    message_id: str = Field(..., description="Unique message ID / idempotency key")
    tenant_id: str = Field(..., description="MSME Merchant phone number in E.164 (+919876543210)")
    sender_phone: str = Field(..., description="Sender phone in E.164 format")
    sender_name: Optional[str] = Field(None, description="Sender display name")
    channel: ChannelType = Field(default=ChannelType.META_WHATSAPP)
    message_type: MessageType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Message content
    text_content: Optional[str] = Field(None, description="Text body if present")
    media_id: Optional[str] = Field(None, description="Channel media identifier for audio/image")
    media_url: Optional[str] = Field(None, description="Direct or downloaded URL of media")
    media_mime_type: Optional[str] = Field(None, description="MIME type of media")
    
    # Interactive response
    interactive_id: Optional[str] = Field(None, description="ID of clicked button or selected list item")
    interactive_title: Optional[str] = Field(None, description="Title of clicked button or selected list item")
    
    # Traceability
    raw_payload: Dict[str, Any] = Field(default_factory=dict, description="Raw original webhook dictionary")
    recipient_phone_id: Optional[str] = Field(None, description="Meta phone number ID the message was sent to")

    model_config = ConfigDict(extra="ignore")


class OutboundMessage(BaseModel):
    """
    Normalized outbound message contract.
    Core ERP & Agent Orchestrator dispatch replies using this contract.
    """
    recipient_phone: str = Field(..., description="Recipient E.164 phone number")
    channel: ChannelType = Field(default=ChannelType.META_WHATSAPP)
    message_type: MessageType = Field(default=MessageType.TEXT)
    
    # Text or body
    text_content: Optional[str] = None
    
    # Interactive buttons (e.g. 1-tap confirmation safety gate)
    interactive_header: Optional[str] = None
    interactive_body: Optional[str] = None
    interactive_footer: Optional[str] = None
    buttons: Optional[List[CanonicalButton]] = None

    # Interactive lists (e.g. 10-language picker)
    list_button_text: Optional[str] = None
    list_sections: Optional[List[CanonicalListSection]] = None
    
    # Media attachments (PDF invoice, KMS report, TTS voice note)
    document_url: Optional[str] = None
    document_filename: Optional[str] = None
    document_bytes: Optional[bytes] = None
    media_id: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    sender_phone_id: Optional[str] = Field(None, description="Meta phone number ID to send message from")

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def set_interactive_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("buttons") and (not data.get("message_type") or data.get("message_type") == MessageType.TEXT):
                data["message_type"] = MessageType.INTERACTIVE_BUTTON
            elif data.get("list_sections") and (not data.get("message_type") or data.get("message_type") == MessageType.TEXT):
                data["message_type"] = MessageType.INTERACTIVE_LIST
            if not data.get("text_content") and data.get("interactive_body"):
                hdr = f"*{data['interactive_header']}*\n\n" if data.get("interactive_header") else ""
                ftr = f"\n\n_{data['interactive_footer']}_" if data.get("interactive_footer") else ""
                data["text_content"] = f"{hdr}{data['interactive_body']}{ftr}".strip()
        return data
