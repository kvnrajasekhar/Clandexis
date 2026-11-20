"""Safe operational error reporting for public application routes."""

import logging
import traceback
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException


def record_exception(logger: logging.Logger, operation: str, exc: Exception) -> str:
    """Log a traceable failure without serializing exception values or request data."""
    error_id = uuid4().hex[:12]
    frames = traceback.extract_tb(exc.__traceback__)
    location = " <- ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-6:]
    )
    logger.error(
        "operation_failed operation=%s error_id=%s exception_type=%s location=%s",
        operation,
        error_id,
        type(exc).__name__,
        location or "unavailable",
    )
    return error_id


def unavailable(
    logger: logging.Logger,
    operation: str,
    exc: Exception,
    public_message: str = "Processing unavailable",
) -> HTTPException:
    """Build a secret-safe 503 response whose reference matches the server log."""
    error_id = record_exception(logger, operation, exc)
    return HTTPException(
        status_code=503,
        detail=f"{public_message}. Reference: {error_id}",
        headers={"X-Clandexis-Error-ID": error_id},
    )
