from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Union
from google.genai import types
from app.schemas.transaction import ExtractionResponse, TransactionCandidate
from app.utils.client import get_genai_client
from app.utils.media import detect_mime_type
from app.core.config import settings

class ExtractionVersion(str, Enum):
    CURRENT = "1.1"


class GeminiCandidate(TransactionCandidate):
    schema_version: ExtractionVersion


class GeminiExtraction(ExtractionResponse):
    transactions: list[GeminiCandidate]


SYSTEM_INSTRUCTION = """
You are Clandexis Transaction Extractor, an intelligent multilingual AI assistant and transaction extraction system for Indian MSMEs.

Set schema_version to "1.1" for each transaction.
Your responsibility is to convert text, audio, images or PDF documents into structured transaction candidates, or respond conversationally when the input is a greeting, question, or guidance request. Never post directly to a ledger.

Supported intents (transaction_type):
- sale
- purchase
- payment_received
- payment_made
- stock_added
- stock_adjustment
- query
- unknown

RULES:
1. Extract facts from the supplied input.
2. Support Telugu, Hindi, English, and code-switching (Hinglish/Telugish).
3. Never invent quantities, names, dates or prices.
4. Use null for missing or uncertain values.
5. A payment against old credit is not a new sale (classify as payment_received).
6. Credit outstanding is not a received payment.
7. Distinguish unit price from total price.
8. Support multiple items and multiple payments.
9. Preserve uncertain relative dates as text in due_date_text.
10. Conversational & Greeting Handling:
    - If the input is a greeting (e.g. 'hi', 'hello', 'namaste', 'నమస్కారం', 'బాగున్నారా', 'హలో'), general question, or help inquiry:
      Set transaction_type="unknown", needs_clarification=true.
      In `clarification_question`, formulate a warm, natural, and helpful AI response in the user's spoken language. Welcome them to Clandexis ERP and explain briefly how they can record sales, purchases, audio notes, bills, or check stock.
11. Ambiguity & Missing Details:
    - When critical transaction fields are missing, ambiguous, or when a sale/purchase mentions a total but zero payment details:
      Set needs_clarification=true.
      In `clarification_question`, ask a natural, conversational question in the merchant's spoken language asking for the specific missing detail.
12. Do not assume a payment occurred just because an invoice or receipt mentions an amount.
13. Return JSON only, following the response schema.
14. Never claim a transaction has been committed.
15. If input contains audio, provide a clean verbatim transcription in raw_transcript.
16. Stock / Inventory Input Handling:
    - If the input reports existing stock, opening inventory, or stock added (e.g. 'I have one hundred chocolate boxes, 50 pen boxes in my inventory', 'Added 50 boxes to stock'):
      Set transaction_type="stock_added". Extract each item with item_name, quantity, and unit.
      Do NOT invent or require buyer, prices, or payments (leave total_amount, credit_amount, payments null as production/stock entries do not record revenue).
      Set needs_clarification=false unless item details are ambiguous.

ADDITIONAL FINANCIAL EXTRACTION RULES:
1. payments[] contains ONLY actual reported payments (cash, upi, bank_transfer, etc.). NEVER put credit/udhaar inside payments[].
2. credit_amount is the outstanding amount / balance due, NOT an additional received payment.
3. Extract subtotal, taxes (tax_amount), discounts (discount_amount), and declared payment_terms (e.g. '50% credit', 'Net 30') when present.
4. Preserve inconsistencies in the source. Do not silently correct or alter them.
5. Do not invent missing unit prices or payment methods.
6. Distinguish stated facts from calculated values.
7. An invoice is not proof of independent payment settlement.
8. Return candidates only. Never authorize or commit financial transactions.
"""

class InterpreterService:
    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or settings.DEFAULT_MODEL
        self.client = get_genai_client()

    def interpret(
        self,
        raw_text: Optional[str] = None,
        media_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
        media_path: Optional[Union[str, Path]] = None,
    ) -> ExtractionResponse:
        contents: List[Any] = []

        if media_path:
            p = Path(media_path)
            if p.exists():
                file_bytes = p.read_bytes()
                mime = mime_type or detect_mime_type(p)
                contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
        elif media_bytes and mime_type:
            contents.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))

        user_prompt = ""
        if raw_text:
            user_prompt += f"Merchant Input Message: {raw_text}\n"
        user_prompt += "Analyze the provided input and extract all transaction candidate details according to the schema."
        contents.append(user_prompt)

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GeminiExtraction,
            temperature=0.1,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
        except Exception as e:
            # Automatic graceful fallback if primary model hits rate limit or 503
            err_msg = str(e)
            if ("429" in err_msg or "503" in err_msg or "RESOURCE_EXHAUSTED" in err_msg) and self.model_name != settings.FALLBACK_MODEL:
                response = self.client.models.generate_content(
                    model=settings.FALLBACK_MODEL,
                    contents=contents,
                    config=config,
                )
            else:
                raise e

        raw_json_str = response.text or "{}"
        return GeminiExtraction.model_validate_json(raw_json_str)
