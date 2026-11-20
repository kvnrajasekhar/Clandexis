import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Tuple, Union

MIME_EXT_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/m4a",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",  # WhatsApp voice notes often come as opus/ogg
    ".aac": "audio/aac",
}

def detect_mime_type(file_path: Union[str, Path], file_bytes: Optional[bytes] = None) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in MIME_EXT_MAP:
        return MIME_EXT_MAP[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"

def compute_payload_fingerprint(
    merchant_id: str,
    raw_text: Optional[str] = None,
    media_bytes: Optional[bytes] = None,
    message_id: Optional[str] = None,
) -> str:
    """
    Generate an idempotency key/fingerprint to identify duplicate requests.
    If message_id is provided from WhatsApp, uses that alongside merchant_id;
    otherwise computes SHA-256 hash of the content payload.
    """
    hasher = hashlib.sha256()
    hasher.update(merchant_id.encode("utf-8"))

    if message_id:
        hasher.update(f":msg:{message_id}".encode("utf-8"))
    else:
        if raw_text:
            hasher.update(f":text:{raw_text.strip()}".encode("utf-8"))
        if media_bytes:
            hasher.update(b":media:" + hashlib.md5(media_bytes).digest())

    return hasher.hexdigest()
