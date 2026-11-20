"""
Meta WhatsApp Business Cloud API (Graph API v21.0) Data Models.
Comprehensive Pydantic v2 schemas for Inbound Webhooks, Outbound Messages,
Interactive 1-Tap Buttons, Media Attachments, and Status Updates.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, ConfigDict


# ==============================================================================
# Enums
# ==============================================================================

class MetaMessageType(str, Enum):
    TEXT = "text"
    AUDIO = "audio"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    STICKER = "sticker"
    VIDEO = "video"
    INTERACTIVE = "interactive"
    BUTTON = "button"
    LOCATION = "location"
    CONTACTS = "contacts"
    ORDER = "order"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class MetaDeliveryStatus(str, Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class MetaInteractiveType(str, Enum):
    BUTTON = "button"
    BUTTON_REPLY = "button_reply"
    LIST = "list"
    LIST_REPLY = "list_reply"
    PRODUCT = "product"
    PRODUCT_LIST = "product_list"


# ==============================================================================
# Inbound Webhook Payload Models
# ==============================================================================

class MetaProfile(BaseModel):
    name: str = Field(..., description="WhatsApp user display name")


class MetaContact(BaseModel):
    profile: Optional[MetaProfile] = None
    wa_id: str = Field(..., description="WhatsApp phone number in international format without '+'")


class MetaTextMessage(BaseModel):
    body: str = Field(..., description="Text content of the incoming message")


class MetaMediaMessage(BaseModel):
    id: str = Field(..., description="Media ID on Meta Graph API servers")
    mime_type: str = Field(..., description="MIME type of media (e.g., audio/ogg; codecs=opus, image/jpeg)")
    sha256: Optional[str] = Field(None, description="SHA256 checksum of media content")
    caption: Optional[str] = Field(None, description="Optional user caption for images/documents")
    filename: Optional[str] = Field(None, description="Filename for document media")
    voice: Optional[bool] = Field(False, description="True if voice note recorded directly in WhatsApp")


class MetaButtonReply(BaseModel):
    id: str = Field(..., description="Unique developer-defined ID of clicked button (e.g., 'CONFIRM_SALE_123')")
    title: str = Field(..., description="User-facing title of clicked button (e.g., 'Confirm & Generate PDF')")


class MetaListReply(BaseModel):
    id: str = Field(..., description="Unique developer-defined ID of selected list item")
    title: str = Field(..., description="User-facing title of selected list item")
    description: Optional[str] = Field(None, description="Optional description of selected list item")


class MetaInteractiveMessage(BaseModel):
    type: MetaInteractiveType
    button_reply: Optional[MetaButtonReply] = None
    list_reply: Optional[MetaListReply] = None


class MetaContext(BaseModel):
    from_user: Optional[str] = Field(None, alias="from")
    id: Optional[str] = Field(None, description="Message ID of the message this is a reply to")
    referred_product: Optional[Dict[str, Any]] = None


class MetaError(BaseModel):
    code: int
    title: str
    message: Optional[str] = None
    error_data: Optional[Dict[str, Any]] = None


class MetaMessage(BaseModel):
    id: str = Field(..., description="Unique WhatsApp message ID (e.g., 'wamid.HBgM...')")
    from_user: str = Field(..., alias="from", description="Sender WhatsApp phone number without '+'")
    timestamp: str = Field(..., description="Unix timestamp in seconds when message was sent")
    type: MetaMessageType = Field(..., description="Message payload type")
    
    # Message type specific payloads
    text: Optional[MetaTextMessage] = None
    audio: Optional[MetaMediaMessage] = None
    voice: Optional[MetaMediaMessage] = None
    image: Optional[MetaMediaMessage] = None
    document: Optional[MetaMediaMessage] = None
    interactive: Optional[MetaInteractiveMessage] = None
    context: Optional[MetaContext] = None
    errors: Optional[List[MetaError]] = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MetaStatusMessage(BaseModel):
    id: str = Field(..., description="WhatsApp message ID for which status is reported")
    status: MetaDeliveryStatus
    timestamp: str
    recipient_id: str
    conversation: Optional[Dict[str, Any]] = None
    pricing: Optional[Dict[str, Any]] = None
    errors: Optional[List[MetaError]] = None


class MetaMetadata(BaseModel):
    display_phone_number: str
    phone_number_id: str = Field(..., description="Meta Cloud API Phone Number ID")


class MetaValue(BaseModel):
    messaging_product: Literal["whatsapp"] = "whatsapp"
    metadata: MetaMetadata
    contacts: Optional[List[MetaContact]] = Field(default_factory=list)
    messages: Optional[List[MetaMessage]] = Field(default_factory=list)
    statuses: Optional[List[MetaStatusMessage]] = Field(default_factory=list)
    errors: Optional[List[MetaError]] = None


class MetaChange(BaseModel):
    value: MetaValue
    field: Literal["messages"] = "messages"


class MetaEntry(BaseModel):
    id: str = Field(..., description="WhatsApp Business Account (WABA) ID")
    changes: List[MetaChange]


class MetaWebhookPayload(BaseModel):
    object: Literal["whatsapp_business_account"] = "whatsapp_business_account"
    entry: List[MetaEntry]

    model_config = ConfigDict(extra="ignore")


# ==============================================================================
# Outbound Message Request Models (Send API)
# ==============================================================================

class MetaTextPayload(BaseModel):
    preview_url: bool = False
    body: str


class MetaMediaPayload(BaseModel):
    id: Optional[str] = None
    link: Optional[str] = None
    caption: Optional[str] = None
    filename: Optional[str] = None


class MetaInteractiveButtonReply(BaseModel):
    id: str
    title: str


class MetaInteractiveButton(BaseModel):
    type: Literal["reply"] = "reply"
    reply: MetaInteractiveButtonReply


class MetaInteractiveHeader(BaseModel):
    type: Literal["text", "video", "image", "document"] = "text"
    text: Optional[str] = None


class MetaInteractiveBody(BaseModel):
    text: str


class MetaInteractiveFooter(BaseModel):
    text: Optional[str] = None


class MetaInteractiveRow(BaseModel):
    id: str
    title: str
    description: Optional[str] = None


class MetaInteractiveSection(BaseModel):
    title: Optional[str] = None
    rows: List[MetaInteractiveRow] = Field(default_factory=list)


class MetaInteractiveAction(BaseModel):
    button: Optional[str] = None
    buttons: Optional[List[MetaInteractiveButton]] = None
    sections: Optional[List[MetaInteractiveSection]] = None


class MetaInteractivePayload(BaseModel):
    type: Literal["button", "list"] = "button"
    header: Optional[MetaInteractiveHeader] = None
    body: MetaInteractiveBody
    footer: Optional[MetaInteractiveFooter] = None
    action: MetaInteractiveAction


class MetaSendMessageRequest(BaseModel):
    messaging_product: Literal["whatsapp"] = "whatsapp"
    recipient_type: Literal["individual"] = "individual"
    to: str = Field(..., description="Recipient phone number with country code, no '+' sign")
    type: MetaMessageType
    
    text: Optional[MetaTextPayload] = None
    audio: Optional[MetaMediaPayload] = None
    image: Optional[MetaMediaPayload] = None
    document: Optional[MetaMediaPayload] = None
    interactive: Optional[MetaInteractivePayload] = None

    model_config = ConfigDict(extra="ignore")


# ==============================================================================
# Outbound Send API Response
# ==============================================================================

class MetaSentMessageItem(BaseModel):
    id: str = Field(..., description="Unique WhatsApp message ID of sent message")


class MetaSendResponse(BaseModel):
    messaging_product: str = "whatsapp"
    contacts: Optional[List[Dict[str, Any]]] = None
    messages: List[MetaSentMessageItem]
