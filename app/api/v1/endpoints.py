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

class TextExtractionRequest(BaseModel):
    merchant_id: str = Field(description="Unique merchant ID or phone number")
    message_id: Optional[str] = Field(default=None, description="WhatsApp or channel message ID")
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
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Processing unavailable")

@router.post("/extract/media", response_model=ProcessingResult, summary="Extract & Validate Transaction from Audio, Image, or PDF")
async def extract_from_media(
    merchant_id: str = Form(...),
    message_id: Optional[str] = Form(None),
    raw_text: Optional[str] = Form(None),
    merchant_business_name: Optional[str] = Form(None),
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
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Processing unavailable")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

@router.post("/speech/synthesize", response_model=TextToSpeechResponse, summary="Text-to-Speech (TTS)")
async def generate_speech(payload: TextToSpeechRequest):
    """
    Synthesize confirmation text or clarification question into an audio file.
    """
    try:
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".aiff")
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
async def download_speech(audio_path: str):
    p = Path(audio_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(p, media_type="audio/aiff", filename=p.name)
