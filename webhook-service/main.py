"""
FastAPI Webhook Service for Clandexis Invisible ERP on Google Cloud Run.
Provides Webhook endpoints for Meta WhatsApp Business Cloud API (Graph API v21.0),
HMAC-SHA256 signature verification, and Outbound dispatching.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse

from config import settings
from models.canonical import InboundMessage, MessageType, OutboundMessage
from app.api.v1 import api_router
from .meta_whatsapp_handler import MetaWhatsAppService
from .security import verify_meta_signature

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("webhook-service")

meta_service: Optional[MetaWhatsAppService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global meta_service
    meta_service = MetaWhatsAppService()
    try:
        from app.services.confirmation import configure_confirmation
        from app.services.persistence import confirmation_adapter
        configure_confirmation(confirmation_adapter())
        logger.info("WhatsApp transport and %s ledger adapter initialized.", settings.DATABASE_BACKEND)
    except Exception as e:
        logger.warning("Ledger adapter unavailable (%s)", type(e).__name__)
    yield
    if meta_service:
        await meta_service.close()
        logger.info("MetaWhatsAppService closed.")


app = FastAPI(
    title="Clandexis Invisible ERP — Webhook & Dashboard API",
    description="WhatsApp Meta Cloud API Ingestion, Ledger Operations & Judge Live Dashboard",
    version="1.1.0",
    lifespan=lifespan,
)

from app.core.security import install_security, require_service
install_security(app)

# Mount all /api/v1 endpoints including dashboard & multimodal extraction
app.include_router(api_router, prefix="/api/v1")

# Mount built React Vite dashboard static assets if present
from pathlib import Path
from fastapi.staticfiles import StaticFiles

dashboard_dist = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if dashboard_dist.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dist), html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root visitors directly to the WhatsApp ERP conversational bot."""
    return RedirectResponse(
        url=settings.whatsapp_chat_url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )



# ==============================================================================
# Health Check Endpoint
# ==============================================================================

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {
        "status": "healthy",
        "service": "webhook-service",
        "channel": "meta_whatsapp",
        "api_version": "v21.0",
        "database_backend": settings.DATABASE_BACKEND,
    }


# ==============================================================================
# Meta WhatsApp Webhook Endpoints
# ==============================================================================

@app.get("/webhook")
async def verify_meta_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    """
    Webhook verification challenge from Meta Developers portal.
    Must return hub.challenge if hub.verify_token matches META_VERIFY_TOKEN.
    """
    expected_verify_token = settings.META_VERIFY_TOKEN
    
    if expected_verify_token and hub_mode == "subscribe" and hub_verify_token == expected_verify_token:
        logger.info("Meta webhook verified successfully.")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)

    logger.warning("Meta webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def handle_meta_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
):
    """
    Inbound Webhook receiver for Meta WhatsApp messages (voice, image, text, interactive buttons).
    1. Validates HMAC-SHA256 signature.
    2. Parses into canonical InboundMessage.
    3. Queues background processing.
    4. Returns HTTP 200 immediately.
    """
    raw_body = await request.body()
    app_secret = settings.META_APP_SECRET or ""

    # 1. Verify HMAC Signature
    if not verify_meta_signature(raw_body, x_hub_signature_256, app_secret):
        raise HTTPException(status_code=403, detail="Invalid HMAC-SHA256 signature")

    raw_json = await request.json()
    if not meta_service:
        raise HTTPException(status_code=500, detail="WhatsApp service not ready")

    # 2. Parse and normalize registered messages
    inbound_messages = meta_service.parse_webhook_payload(raw_json)
    
    for msg in inbound_messages:
        logger.info(
            f"Received InboundMessage: id={msg.message_id}, type={msg.message_type}, "
            f"sender={msg.sender_phone}, interactive_id={msg.interactive_id}"
        )
        # Process inline so Cloud Run keeps CPU allocated until the reply is sent
        try:
            await process_inbound_message(msg)
        except Exception as e:
            logger.error(f"Failed to process message {msg.message_id}: {e}", exc_info=True)

    # 3. Handle unregistered senders via dynamic WhatsApp onboarding workflow
    unregistered_messages = []
    if settings.ENABLE_WHATSAPP_ONBOARDING:
        unregistered_messages = meta_service.parse_unregistered_inbound_messages(raw_json)
        for unreg_msg in unregistered_messages:
            logger.info(
                f"Received Onboarding InboundMessage: id={unreg_msg.message_id}, type={unreg_msg.message_type}, "
                f"sender={unreg_msg.sender_phone}, interactive_id={unreg_msg.interactive_id}"
            )
            try:
                await process_onboarding_message(unreg_msg)
            except Exception as e:
                logger.error(f"Failed to process onboarding message {unreg_msg.message_id}: {e}", exc_info=True)

    total_received = len(inbound_messages) + len(unregistered_messages)
    return {"status": "ok", "messages_received": total_received}


async def process_onboarding_message(message: InboundMessage):
    """
    Background worker passing unapproved / unregistered merchant message to Onboarding State Machine.
    """
    logger.info(f"Processing onboarding message {message.message_id} for {message.sender_phone}...")
    try:
        from app.services.merchant_onboarding import MerchantOnboardingService
        service = MerchantOnboardingService()
        media_bytes = None
        if message.message_type in (MessageType.AUDIO, MessageType.IMAGE, MessageType.DOCUMENT):
            if meta_service is not None and message.media_id and message.media_mime_type:
                media_bytes = await meta_service.download_media_bytes(message.media_id)
        outbound = await service.handle_onboarding_message(message, media_bytes=media_bytes)
        if meta_service and outbound:
            await meta_service.dispatch_outbound(outbound)
    except Exception as e:
        logger.error(f"Onboarding flow failed for {message.sender_phone}: {e}", exc_info=True)


async def process_inbound_message(message: InboundMessage):
    """
    Background worker passing normalized message to Agent Orchestrator.
    """
    logger.info(f"Processing inbound message {message.message_id} in background pipeline...")
    try:
        from app.services.whatsapp_conversation import process_whatsapp_message
        media_bytes = None
        if message.message_type in (MessageType.AUDIO, MessageType.IMAGE, MessageType.DOCUMENT):
            if meta_service is None:
                return
            if message.media_id and message.media_mime_type:
                media_bytes = await meta_service.download_media_bytes(message.media_id)
            if not media_bytes:
                await meta_service.dispatch_outbound(OutboundMessage(
                    recipient_phone=message.sender_phone,
                    text_content="Could not read the attachment. Please resend it or send the details as text."))
                return
        result, outbound = process_whatsapp_message(message, media_bytes=media_bytes)
        logger.info("Orchestration completed for %s (%s)", message.message_id, type(result).__name__)
        
        # Generate only after a successful ledger result; all side effects are
        # outside Firestore callbacks. Invoice errors never undo the committed sale.
        from app.services.confirmation import AuthenticatedMerchant, PostingResult
        from app.services.posting_flow import generate_posting_document
        document = None
        if isinstance(result, PostingResult):
            document = generate_posting_document(AuthenticatedMerchant(message.tenant_id), result)
            if document["status"] != "NOT_APPLICABLE":
                outbound.text_content += f"\nInvoice status: {document['status']}"
        if meta_service:
            await meta_service.dispatch_outbound(outbound)
            if document and document.get("document_url"):
                await meta_service.dispatch_outbound(OutboundMessage(
                    recipient_phone=message.sender_phone, message_type=MessageType.DOCUMENT,
                    document_url=document["document_url"], document_filename=document["filename"]))
    except Exception as e:
        logger.error("Inbound processing failed (%s)", type(e).__name__)


# ==============================================================================
# Outbound Message Dispatch API
# ==============================================================================

@app.post("/api/v1/send")
async def send_outbound_message(outbound: OutboundMessage, authorized=Depends(require_service)):
    """
    Internal API for microservices (agent-orchestrator, pdf-service) to dispatch
    messages, 1-tap confirmation buttons, PDF invoices, and audio readbacks to WhatsApp.
    """
    if not meta_service:
        raise HTTPException(status_code=500, detail="WhatsApp service not ready")

    resp = await meta_service.dispatch_outbound(outbound)
    if not resp:
        raise HTTPException(status_code=502, detail="Failed to dispatch message to Meta WhatsApp Cloud API")

    return {
        "status": "dispatched",
        "messages": resp.messages,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook_service.main:app", host=settings.HOST, port=settings.PORT, reload=not settings.is_production)
