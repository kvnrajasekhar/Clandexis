import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.pipeline import PipelineService
from app.schemas.transaction import ProcessingResult
from app.schemas.speech import TextToSpeechRequest, TextToSpeechResponse
from app.services.speech import synthesize_speech

from app.core.security import SessionIdentity, dashboard_identity

router = APIRouter(dependencies=[Depends(dashboard_identity)])
pipeline = None

def get_pipeline():
    global pipeline
    if pipeline is None:
        pipeline = PipelineService()
    return pipeline

from app.services.confirmation import AuthenticatedMerchant, get_confirmation_service


def stage_for_dashboard(result, identity):
    if result.input_metadata.is_duplicate:
        return
    for index, candidate in enumerate(result.extraction.transactions):
        if candidate.validation_status != "REJECTED" and candidate.transaction_type not in ("unknown", "query"):
            get_confirmation_service().stage(AuthenticatedMerchant(identity.merchant_id),
                result.input_metadata.idempotency_key, index, candidate)


class TextExtractionRequest(BaseModel):
    merchant_id: str = Field(description="Unique merchant ID or phone number")
    message_id: Optional[str] = Field(default=None, description="WhatsApp or channel message ID")
    stage: bool = False
    raw_text: str = Field(description="Merchant raw text message")
    merchant_business_name: Optional[str] = Field(default=None, description="Registered merchant business name")

@router.post("/extract/text", response_model=ProcessingResult, summary="Extract & Validate Transaction from Text")
async def extract_from_text(payload: TextExtractionRequest, identity: SessionIdentity = Depends(dashboard_identity)):
    """
    Process raw WhatsApp/SMS text message:
    - Input processing & deduplication
    - Gemini multimodal extraction
    - Stage 4 Business validation
    """
    if payload.merchant_id != identity.merchant_id:
        raise HTTPException(403, "Merchant access denied")
    try:
        result = get_pipeline().process_and_interpret(
            merchant_id=payload.merchant_id,
            message_id=payload.message_id,
            raw_text=payload.raw_text,
            merchant_business_name=payload.merchant_business_name,
        )
        if payload.stage:
            stage_for_dashboard(result, identity)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Processing unavailable")

@router.post("/extract/media", response_model=ProcessingResult, summary="Extract & Validate Transaction from Audio, Image, or PDF")
async def extract_from_media(
    merchant_id: str = Form(...),
    message_id: Optional[str] = Form(None),
    raw_text: Optional[str] = Form(None),
    merchant_business_name: Optional[str] = Form(None),
    stage: bool = Form(False),
    file: UploadFile = File(...),
    identity: SessionIdentity = Depends(dashboard_identity),
):
    """
    Process uploaded voice note (mp3/ogg/wav), receipt image (png/jpg), or PDF document:
    - Direct stream & media handling
    - Multimodal Gemini interpretation
    - Stage 4 Business arithmetic & terms validation
    """
    if merchant_id != identity.merchant_id:
        raise HTTPException(403, "Merchant access denied")
    # Write uploaded stream to a secure temp file
    suffix = Path(file.filename or "media.tmp").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = get_pipeline().process_and_interpret(
            merchant_id=merchant_id,
            message_id=message_id,
            raw_text=raw_text,
            media_path=tmp_path,
            merchant_business_name=merchant_business_name,
        )
        if stage:
            stage_for_dashboard(result, identity)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Processing unavailable")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

def speech_directory(identity):
    tenant = hashlib.sha256(identity.merchant_id.encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "clandexis-speech" / tenant
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory.resolve()


@router.post("/speech/synthesize", response_model=TextToSpeechResponse, summary="Text-to-Speech (TTS)")
async def generate_speech(payload: TextToSpeechRequest, identity: SessionIdentity = Depends(dashboard_identity)):
    """
    Synthesize confirmation text or clarification question into an audio file.
    """
    try:
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".aiff", dir=speech_directory(identity))
        temp_audio.close()
        out_path = synthesize_speech(
            text=payload.text,
            output_audio_path=temp_audio.name,
            voice=payload.voice,
        )
        return TextToSpeechResponse(
            status="success",
            message="Speech audio generated successfully",
            audio_path=out_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Processing unavailable")

@router.get("/speech/download", summary="Download generated audio")
async def download_speech(audio_path: str, identity: SessionIdentity = Depends(dashboard_identity)):
    p = Path(audio_path).resolve()
    if p.parent != speech_directory(identity) or p.suffix != ".aiff" or not p.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(p, media_type="audio/aiff", filename=p.name)
