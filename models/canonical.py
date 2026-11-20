"""
Canonical Channel Adapter Models.
Decouples channel-specific protocols (Meta WhatsApp Cloud API, Twilio, Web Simulator)
from core AI reasoning, human-in-the-loop safety gates, and atomic ledger mutations.
(FR-06 & FR-07 Omnichannel Plug-and-Play Specification).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


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
    id: str = Field(..., description="Developer defined action identifier, e.g. 'CONFIRM_TXN_123'")
    title: str = Field(..., max_length=20, description="Short user-facing button label, e.g. 'Confirm (हाँ)'")


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
    
    # Media attachments (PDF invoice, KMS report, TTS voice note)
    document_url: Optional[str] = None
    document_filename: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None

    model_config = ConfigDict(extra="ignore")
