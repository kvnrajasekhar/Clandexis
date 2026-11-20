from pathlib import Path
from typing import Optional, Union
from app.schemas.transaction import ProcessingResult, ExtractionResponse, TransactionCandidate, TransactionType
from app.services.input_processor import InputProcessor
from app.services.interpreter import InterpreterService
from app.services.validator import BusinessValidator
from app.core.config import settings

class PipelineService:
    """
    Core End-to-End Orchestrator:
    - Stage 2: Input processing & Idempotency filter
    - Stage 3: Gemini Multimodal Candidate Extraction
    - Stage 4: Stage 4 Business Validation & Reconciliation
    """
    def __init__(self, model_name: Optional[str] = None):
        self.input_processor = InputProcessor()
        self.interpreter = InterpreterService(model_name=model_name or settings.DEFAULT_MODEL)
        self.validator = BusinessValidator()

    def process_and_interpret(
        self,
        merchant_id: str,
        message_id: Optional[str] = None,
        raw_text: Optional[str] = None,
        media_path: Optional[Union[str, Path]] = None,
        media_bytes: Optional[bytes] = None,
        merchant_business_name: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> ProcessingResult:
        # Stage 2: Input processing & Idempotency
        payload_meta, f_bytes, mime_type = self.input_processor.process(
            merchant_id=merchant_id,
            message_id=message_id,
            raw_text=raw_text,
            media_path=media_path,
            media_bytes=media_bytes,
            mime_type=mime_type,
        )

        if payload_meta.is_duplicate:
            return ProcessingResult(
                input_metadata=payload_meta,
                extraction=ExtractionResponse(
                    transactions=[
                        TransactionCandidate(
                            transaction_type=TransactionType.UNKNOWN,
                            confidence=0.0,
                            missing_fields=["duplicate_request"],
                            needs_clarification=False,
                            clarification_question="Duplicate request detected and ignored.",
                            validation_status="REJECTED",
                            requires_review=True,
                            validation_warnings=["Duplicate request prevented by Stage 2 filter. Ledger posting blocked."]
                        )
                    ]
                )
            )

        # Stage 3: Gemini Multimodal Interpretation
        raw_extraction = self.interpreter.interpret(
            raw_text=raw_text,
            media_bytes=f_bytes,
            mime_type=mime_type,
            media_path=media_path,
        )

        # Stage 4: Business Validation & Financial Reconciliation
        current_name = merchant_business_name or merchant_id
        validated_extraction = self.validator.validate_response(
            raw_extraction, current_merchant_id_or_name=current_name
        )

        return ProcessingResult(
            input_metadata=payload_meta,
            extraction=validated_extraction,
        )

__all__ = ["PipelineService", "InputProcessor", "InterpreterService", "BusinessValidator"]
