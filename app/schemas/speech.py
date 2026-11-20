from pydantic import BaseModel, Field
from typing import Optional

class TextToSpeechRequest(BaseModel):
    text: str = Field(description="Text to convert to speech")
    voice: Optional[str] = Field(default=None, description="Voice name (e.g. Samantha, Alex)")

class TextToSpeechResponse(BaseModel):
    status: str
    message: str
    audio_path: Optional[str] = None

class ClarificationPayload(BaseModel):
    merchant_id: str
    original_message_id: str
    question: str
