import time
from pathlib import Path
from typing import Optional, Set, Tuple, Union
from app.schemas.transaction import ProcessedInputPayload
from app.utils.media import compute_payload_fingerprint, detect_mime_type

class InputProcessor:
    """
    Stage 2: Input Processing
    - Identifies merchant and message
    - Handles incoming media (voice notes, images, PDFs, text)
    - Prevents duplicate requests (Idempotency check)
    """

    def __init__(self, ttl_seconds: int = 86400):
        # In-memory deduplication cache (backend can plug in Redis / DB here)
        self._processed_keys: Set[str] = set()
        self.ttl_seconds = ttl_seconds

    def process(
        self,
        merchant_id: str,
        message_id: Optional[str] = None,
        raw_text: Optional[str] = None,
        media_path: Optional[Union[str, Path]] = None,
        media_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
    ) -> Tuple[ProcessedInputPayload, Optional[bytes], Optional[str]]:
        """
        Process incoming merchant message and return normalized payload.
        """
        if not merchant_id:
            raise ValueError("merchant_id is required.")

        file_bytes = media_bytes

        if media_path:
            p = Path(media_path)
            if p.exists():
                if file_bytes is None:
                    file_bytes = p.read_bytes()
                mime_type = detect_mime_type(p)

        # Generate deduplication idempotency key
        idempotency_key = compute_payload_fingerprint(
            merchant_id=merchant_id,
            raw_text=raw_text,
            media_bytes=file_bytes,
            message_id=message_id,
        )

        is_dup = idempotency_key in self._processed_keys
        if not is_dup:
            self._processed_keys.add(idempotency_key)

        # Classify input media type
        if file_bytes:
            if mime_type and "audio" in mime_type:
                media_type = "voice"
            elif mime_type and "image" in mime_type:
                media_type = "image"
            elif mime_type and "pdf" in mime_type:
                media_type = "document"
            else:
                media_type = "media"
        else:
            media_type = "text"

        payload = ProcessedInputPayload(
            merchant_id=merchant_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            is_duplicate=is_dup,
            media_type=media_type,
            media_path=str(media_path) if media_path else None,
            raw_text=raw_text,
        )

        return payload, file_bytes, mime_type
