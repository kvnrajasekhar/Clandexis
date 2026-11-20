"""
FastAPI Webhook Service for Clandexis Invisible ERP on Google Cloud Run.
Provides Webhook endpoints for Meta WhatsApp Business Cloud API (Graph API v21.0),
HMAC-SHA256 signature verification, and Outbound dispatching.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from config import settings
from models.canonical import InboundMessage, OutboundMessage
from .meta_whatsapp_handler import MetaWhatsAppService
from .security import verify_meta_signature

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("webhook-service")

meta_service: Optional[MetaWhatsAppService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global meta_service
    meta_service = MetaWhatsAppService()
    logger.info("MetaWhatsAppService initialized.")
    yield
    if meta_service:
        await meta_service.close()
        logger.info("MetaWhatsAppService closed.")


app = FastAPI(
    title="Clandexis Invisible ERP — Webhook Service",
    description="WhatsApp Meta Cloud API Ingestion and Delivery Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    
    if hub_mode == "subscribe" and hub_verify_token == expected_verify_token:
        logger.info("Meta webhook verified successfully.")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)

    logger.warning(f"Meta webhook verification failed. Token received: {hub_verify_token}")
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

    # 2. Parse and normalize
    inbound_messages = meta_service.parse_webhook_payload(raw_json)
    
    for msg in inbound_messages:
        logger.info(
            f"Received InboundMessage: id={msg.message_id}, type={msg.message_type}, "
            f"sender={msg.sender_phone}, interactive_id={msg.interactive_id}"
        )
        # Process asynchronously in background
        background_tasks.add_task(process_inbound_message, msg)

    return {"status": "ok", "messages_received": len(inbound_messages)}


async def process_inbound_message(message: InboundMessage):
    """
    Background worker passing normalized message to Agent Orchestrator.
    """
    logger.info(f"Processing inbound message {message.message_id} in background pipeline...")
    # This invokes the agent-orchestrator router / extractor
    # (Dev 2 / Agent Orchestrator pipeline)


# ==============================================================================
# Outbound Message Dispatch API
# ==============================================================================

@app.post("/api/v1/send")
async def send_outbound_message(outbound: OutboundMessage):
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
