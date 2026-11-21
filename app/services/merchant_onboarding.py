"""
Merchant Onboarding Workflow & State Machine for Clandexis Invisible ERP.
Provides an intuitive, vernacular-first, multi-modal onboarding experience via WhatsApp:
1. Dynamic language suggestion & 1-tap language selection (Telugu, Hindi, Gujarati, English, etc.)
2. Frictionless phone identity binding & 4-digit security PIN setup
3. Multi-modal MSME detail gathering (Document/Photo OCR, WhatsApp Flows JSON, Voice Note, Conversational Text)
4. Deterministic MSME validation (GSTIN Luhn checksum, Udyam URN regex, PAN, Pincode state lookup)
5. 1-Tap Interactive Confirmation & PostgreSQL Provisioning
"""

import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.conversation_language import (
    SUPPORTED_LANGUAGES,
    get_onboarding_language_list,
    get_onboarding_language_options,
    preference,
)
from app.services.msme_validator import (
    validate_gstin,
    validate_pan,
    validate_pincode,
    validate_udyam,
    validate_upi_vpa,
)
from app.services.postgres_onboarding import PostgresOnboardingStore
from config import settings
from models.canonical import (
    CanonicalButton,
    CanonicalListRow,
    CanonicalListSection,
    InboundMessage,
    MessageType,
    OutboundMessage,
)

logger = logging.getLogger("merchant-onboarding")

_ONBOARDING_BUTTON_RE = re.compile(r"^MSME_ONBOARD_(CONFIRM|EDIT)(?:_([0-9a-f]{16}))?$")


def _is_onboarding_action_id(action_id: str) -> bool:
    return (
        action_id.startswith(("MSME_ONBOARD_", "LANG_"))
        or action_id in ("PIN_SKIP", "ONBOARD_START", "ONBOARD_RESET", "RESET")
    )


_CITY_ADDRESS_MAP = {
    "Guntur": "Guntur, Andhra Pradesh",
    "Dharmavaram": "Dharmavaram, Andhra Pradesh",
    "Vijayawada": "Vijayawada, Andhra Pradesh",
    "Hyderabad": "Hyderabad, Telangana",
    "Sircilla": "Sircilla, Telangana",
    "Surat": "Surat, Gujarat",
    "Coimbatore": "Coimbatore, Tamil Nadu",
    "Madurai": "Madurai, Tamil Nadu",
    "Tiruppur": "Tiruppur, Tamil Nadu",
    "Varanasi": "Varanasi, Uttar Pradesh",
    "Jaipur": "Jaipur, Rajasthan",
    "Ludhiana": "Ludhiana, Punjab",
}


class MSMEExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    business_name: Optional[str] = None
    owner_name: Optional[str] = None
    gstin: Optional[str] = None
    udyam_reg_no: Optional[str] = None
    pan: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    upi_vpa: Optional[str] = None
    detected_language: Optional[str] = None


class OnboardingStep(str, Enum):
    INIT = "INIT"
    AWAITING_LANGUAGE = "AWAITING_LANGUAGE"
    AWAITING_PIN = "AWAITING_PIN"
    AWAITING_MSME_DETAILS = "AWAITING_MSME_DETAILS"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"


class OnboardingSession:
    def __init__(self, phone: str):
        self.phone: str = phone
        self.step: OnboardingStep = OnboardingStep.INIT
        self.preferred_language: str = "en"
        self.script_preference: str = "Latin"
        self.security_pin_hash: Optional[str] = None
        self.business_name: Optional[str] = None
        self.owner_name: Optional[str] = None
        self.gstin: Optional[str] = None
        self.udyam_reg_no: Optional[str] = None
        self.pan_number: Optional[str] = None
        self.business_address: Optional[str] = None
        self.pincode: Optional[str] = None
        self.upi_vpa: Optional[str] = None
        self.confirmation_nonce: Optional[str] = None
        self.editing: bool = False
        self.revision: int = 1
        self.last_updated: datetime = datetime.now(timezone.utc)
        self.raw_extracted: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phone": self.phone,
            "step": self.step.value,
            "preferred_language": self.preferred_language,
            "script_preference": self.script_preference,
            "business_name": self.business_name,
            "owner_name": self.owner_name,
            "gstin": self.gstin,
            "udyam_reg_no": self.udyam_reg_no,
            "pan_number": self.pan_number,
            "business_address": self.business_address,
            "pincode": self.pincode,
            "upi_vpa": self.upi_vpa,
            "confirmation_nonce": self.confirmation_nonce,
            "editing": self.editing,
            "last_updated": self.last_updated.isoformat(),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, state: Dict[str, Any], phone: str) -> "OnboardingSession":
        if not isinstance(state, dict):
            raise ValueError("Persisted onboarding state must be a JSON object")
        stored_phone = state.get("phone")
        if stored_phone and stored_phone != phone:
            raise ValueError("Persisted onboarding state belongs to another phone")

        session = cls(phone)
        try:
            session.step = OnboardingStep(state.get("step", OnboardingStep.INIT.value))
        except ValueError as exc:
            raise ValueError("Persisted onboarding state has an invalid step") from exc
        for field in (
            "preferred_language",
            "script_preference",
            "business_name",
            "owner_name",
            "gstin",
            "udyam_reg_no",
            "pan_number",
            "business_address",
            "pincode",
            "upi_vpa",
            "confirmation_nonce",
        ):
            value = state.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"Persisted onboarding field {field} must be a string")
                setattr(session, field, value)
        editing = state.get("editing", False)
        if not isinstance(editing, bool):
            raise ValueError("Persisted onboarding editing flag must be a boolean")
        session.editing = editing
        revision = state.get("revision", 1)
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("Persisted onboarding revision must be a positive integer")
        session.revision = revision
        last_updated = state.get("last_updated")
        if isinstance(last_updated, str):
            try:
                session.last_updated = datetime.fromisoformat(last_updated)
            except ValueError:
                session.last_updated = datetime.now(timezone.utc)
        return session


# Ephemeral in-memory session cache keyed by phone number
_ONBOARDING_SESSIONS: Dict[str, OnboardingSession] = {}


def get_or_create_session(phone: str) -> OnboardingSession:
    if phone not in _ONBOARDING_SESSIONS:
        _ONBOARDING_SESSIONS[phone] = OnboardingSession(phone)
    return _ONBOARDING_SESSIONS[phone]


class MerchantOnboardingService:
    """
    Orchestrates the entire multi-modal WhatsApp onboarding flow for merchants.
    """

    def __init__(
        self,
        sessions: Optional[Dict[str, OnboardingSession]] = None,
        store: Optional[PostgresOnboardingStore] = None,
    ):
        self.sessions = sessions if sessions is not None else _ONBOARDING_SESSIONS
        self.store = store

    def get_session(self, phone: str) -> OnboardingSession:
        if phone not in self.sessions:
            self.sessions[phone] = OnboardingSession(phone)
        return self.sessions[phone]

    async def handle_onboarding_message(
        self,
        message: InboundMessage,
        *,
        media_bytes: Optional[bytes] = None,
    ) -> OutboundMessage:
        """
        Main entrypoint processing an inbound message from an unapproved / onboarding merchant.
        """
        phone = message.sender_phone
        if self.store is None:
            session = self.get_session(phone)
            return await self._handle_onboarding_message(message, session, media_bytes=media_bytes)

        claim = self.store.claim(message)
        if claim.duplicate:
            session = OnboardingSession.from_dict(claim.state, phone)
            return self._build_duplicate_message(session)
        if claim.busy:
            session = OnboardingSession.from_dict(claim.state, phone)
            return self._build_busy_message(session)
        if claim.token is None:
            raise RuntimeError("The onboarding store returned a claim without a lease token")

        try:
            session = OnboardingSession.from_dict(claim.state, phone)
            outbound = await self._handle_onboarding_message(message, session, media_bytes=media_bytes)
            button_match = _ONBOARDING_BUTTON_RE.fullmatch(message.interactive_id or "")
            activate_merchant = bool(
                session.step == OnboardingStep.COMPLETED
                and button_match
                and button_match.group(1) == "CONFIRM"
            )
            self.store.finish(
                message,
                claim.token,
                session.to_dict(),
                activate_merchant=activate_merchant,
            )
            if session.step == OnboardingStep.COMPLETED:
                self.sessions.pop(phone, None)
            else:
                session.revision = int(claim.state.get("revision", 1)) + 1
                self.sessions[phone] = session
            return outbound
        except Exception:
            self.store.release(message, claim.token)
            raise

    async def _handle_onboarding_message(
        self,
        message: InboundMessage,
        session: OnboardingSession,
        *,
        media_bytes: Optional[bytes] = None,
    ) -> OutboundMessage:
        phone = message.sender_phone
        session.last_updated = datetime.now(timezone.utc)

        # 0. User Reset / Restart Command
        raw_text = (message.text_content or "").strip().lower()
        button_id = message.interactive_id or (message.text_content or "").strip()
        if _is_onboarding_action_id(button_id):
            if session.step == OnboardingStep.COMPLETED or self._merchant_is_registered(phone):
                session.step = OnboardingStep.COMPLETED
                return self._build_stale_onboarding_action_message(session, account_active=True)

        if raw_text in ("reset", "/reset", "restart", "start over", "clear") or button_id in ("ONBOARD_RESET", "RESET"):
            logger.info("Resetting onboarding session for %s by user command", phone)
            session = OnboardingSession(phone)
            self.sessions[phone] = session
            lang, script = preference(message.text_content or "")
            session.preferred_language = lang
            session.script_preference = script
            session.step = OnboardingStep.AWAITING_LANGUAGE
            return self._build_language_menu(session, suggested_lang=lang)

        # 1. Handle Interactive Button Clicks

        # Step A: Language Selection
        if button_id.startswith("LANG_"):
            lang_code = button_id.replace("LANG_", "").lower()
            chosen_lang = lang_code if lang_code in SUPPORTED_LANGUAGES else "en"
            chosen_script = SUPPORTED_LANGUAGES.get(chosen_lang, {}).get("default_script", "Latin")
            session.preferred_language = chosen_lang
            session.script_preference = chosen_script
            # Frictionless onboarding: Direct transition to MSME details (no PIN friction)
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            return self._build_msme_collection_prompt(session)

        # Step B: PIN Action / Skip (backward compatibility)
        if button_id in ("PIN_SKIP", "ONBOARD_START") and session.step in (OnboardingStep.AWAITING_PIN, OnboardingStep.AWAITING_LANGUAGE):
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            return self._build_msme_collection_prompt(session)

        # Step C: MSME Confirmation Buttons
        action_match = _ONBOARDING_BUTTON_RE.fullmatch(button_id)
        if action_match:
            action, nonce = action_match.groups()
            if session.step != OnboardingStep.AWAITING_CONFIRMATION or (
                nonce != session.confirmation_nonce
                and (nonce is not None or session.confirmation_nonce is not None)
            ):
                return self._build_stale_onboarding_action_message(session)
            if action == "CONFIRM":
                return await self._commit_merchant_onboarding(session)
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            session.editing = True
            session.confirmation_nonce = None
            return self._build_msme_collection_prompt(session, is_edit=True)
        if button_id.startswith("MSME_ONBOARD_"):
            return self._build_stale_onboarding_action_message(session)

        # 2. State-Specific Flow Processing
        if session.step == OnboardingStep.INIT:
            # Language detection from message text or script
            lang, script = preference(message.text_content or "")
            session.preferred_language = lang
            session.script_preference = script
            session.step = OnboardingStep.AWAITING_LANGUAGE
            return self._build_language_menu(session, suggested_lang=lang)

        if session.step == OnboardingStep.AWAITING_LANGUAGE:
            # Fallback if user typed text instead of clicking a button/list
            lang, script = preference(message.text_content or "")
            session.preferred_language = lang
            session.script_preference = script
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            return self._build_msme_collection_prompt(session)

        if session.step == OnboardingStep.AWAITING_PIN:
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            return self._build_msme_collection_prompt(session)

        if session.step in (OnboardingStep.AWAITING_MSME_DETAILS, OnboardingStep.AWAITING_CONFIRMATION):
            return await self._process_msme_intake(session, message, media_bytes=media_bytes)

        if session.step == OnboardingStep.COMPLETED:
            from app.services.whatsapp_identity import resolve_phone
            # If PostgreSQL is configured and this phone is no longer found in merchants table,
            # it means database data was cleared/deleted. Reset stale in-memory session.
            if (
                self.store is None
                and settings.DATABASE_BACKEND == "postgres"
                and settings.DATABASE_URL
                and not resolve_phone(phone)
            ):
                logger.warning(
                    "Phone %s has COMPLETED in-memory session but does not exist in PostgreSQL. Resetting session.",
                    phone,
                )
                session = OnboardingSession(phone)
                self.sessions[phone] = session
                lang, script = preference(message.text_content or "")
                session.preferred_language = lang
                session.script_preference = script
                session.step = OnboardingStep.AWAITING_LANGUAGE
                return self._build_language_menu(session, suggested_lang=lang)

            return OutboundMessage(
                recipient_phone=phone,
                message_type=MessageType.TEXT,
                text_content=self._get_already_completed_message(session),
            )

        return self._build_language_menu(session, suggested_lang=session.preferred_language)

    # =========================================================================
    # Prompts & UI Messages
    # =========================================================================

    def _build_language_menu(self, session: OnboardingSession, suggested_lang: str = "en") -> OutboundMessage:
        list_opts = get_onboarding_language_list(suggested_lang)
        sections = [
            CanonicalListSection(
                title=sec.get("title"),
                rows=[
                    CanonicalListRow(id=r["id"], title=r["title"], description=r.get("description"))
                    for r in sec["rows"]
                ]
            )
            for sec in list_opts["sections"]
        ]
        # Also provide buttons for button-only channels
        opts = get_onboarding_language_options(suggested_lang)
        buttons = [
            CanonicalButton(id=btn["id"], title=btn["title"])
            for btn in opts["buttons"]
        ]
        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.INTERACTIVE_LIST,
            interactive_header=list_opts["header"],
            interactive_body=list_opts["body"],
            interactive_footer="Clandexis Invisible ERP",
            list_button_text=list_opts["button_text"],
            list_sections=sections,
            buttons=buttons,
            text_content=f"{list_opts['header']}\n\n{list_opts['body']}",
        )

    def _build_pin_setup_prompt(self, session: OnboardingSession) -> OutboundMessage:
        # Kept for backward compatibility if ever called
        return self._build_msme_collection_prompt(session)

    def _build_msme_collection_prompt(
        self,
        session: OnboardingSession,
        pin_set: bool = False,
        is_retry: bool = False,
        is_edit: bool = False,
    ) -> OutboundMessage:
        lang = session.preferred_language
        editing = is_retry or is_edit

        if lang == "te":
            if editing:
                text = (
                    f"✏️ *వ్యాపార వివరాల సవరణ*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"ఇప్పటికే నమోదు చేసిన వివరాలు భద్రంగా ఉన్నాయి.\n\n"
                    f"మీరు ఏ వివరాలు మార్చాలనుకుంటున్నారో వాయిస్ లేదా టెక్స్ట్ ద్వారా చెప్పండి:\n"
                    f"🎙️ ఉదా: 'నా షాపు పేరు శ్రీ రాజరాజేశ్వరి' లేదా 'చిరునామా గుంటూరు, ఆంధ్రప్రదేశ్'.\n\n"
                    f"మేము ఆ వివరాలను వెంటనే సరిదిద్దుతాము."
                )
            else:
                text = (
                    f"🏢 *మీ MSME వ్యాపార వివరాలు నమోదు చేయండి*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"అత్యంత సులభమైన పద్ధతి:\n"
                    f"📸 మీ *Udyam సర్టిఫికెట్*, *GST సర్టిఫికెట్* లేదా *విజిటింగ్ కార్డు* ఫోటోను ఇక్కడే పంపండి.\n\n"
                    f"లేదా వాయిస్ మెసేజ్ ద్వారా చెప్పండి:\n"
                    f"🎙️ 'నా షాపు పేరు శ్రీ లక్ష్మి హ్యాండ్‌లూమ్స్, ఓనర్ రమేష్, చిరునామా గుంటూరు...'\n\n"
                    f"లేదా వివరాలను నేరుగా మెసేజ్ చేయండి."
                )
        elif lang == "hi":
            if editing:
                text = (
                    f"✏️ *व्यापार विवरण संशोधन*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"पहले से दर्ज विवरण सुरक्षित हैं।\n\n"
                    f"जो जानकारी आप बदलना चाहते हैं, बोलकर या लिखकर भेजें:\n"
                    f"🎙️ उदा: 'दुकान का नाम...' या 'पता सूरत, गुजरात'।\n\n"
                    f"हम तुरंत विवरण अपडेट कर देंगे।"
                )
            else:
                text = (
                    f"🏢 *अपने MSME व्यापार का विवरण दर्ज करें*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"सबसे आसान तरीका:\n"
                    f"📸 अपने *Udyam सर्टिफिकेट*, *GST सर्टिफिकेट* या *विजिटिंग कार्ड* का फोटो भेजें।\n\n"
                    f"या बोलकर वॉयस नोट भेजें:\n"
                    f"🎙️ 'मेरी दुकान का नाम मोहन टेक्सटाइल्स, मालिक मोहन लाल, पता सूरत...'\n\n"
                    f"या टाइप करके भेजें।"
                )
        elif lang == "ta":
            if editing:
                text = (
                    f"✏️ *வணிக விவரங்களை திருத்துதல்*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"முன்பு பதிவு செய்த விவரங்கள் பாதுகாக்கப்படுகின்றன.\n\n"
                    f"நீங்கள் மாற்ற விரும்பும் விவரங்களை குரல் குறிப்பு அல்லது செய்தியாக அனுப்பவும்:\n"
                    f"🎙️ எ.கா: 'கடை பெயர்...' அல்லது 'முகவரி கோயம்புத்தூர்'.\n\n"
                    f"நாங்கள் உடனே புதுப்பிப்போம்."
                )
            else:
                text = (
                    f"🏢 *உங்கள் MSME வணிக விவரங்களை பதிவு செய்யவும்*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"எளிய வழி:\n"
                    f"📸 உங்கள் *Udyam*, *GST* அல்லது *விசிட்டிங் கார்டு* புகைப்படத்தை அனுப்பவும்.\n\n"
                    f"அல்லது குரல் குறிப்பு மூலம் கூறவும்:\n"
                    f"🎙️ 'கடை பெயர் ஸ்ரீ பாலாஜி டெக்ஸ்டைல்ஸ், உரிமையாளர்...' \n\n"
                    f"அல்லது தட்டச்சு செய்து அனுப்பவும்."
                )
        else:
            if editing:
                text = (
                    f"✏️ *Edit MSME Business Details*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"Previously entered details are safely preserved.\n\n"
                    f"Please state what you'd like to update:\n"
                    f"🎙️ e.g., 'Shop name is...' or 'Address is Guntur, AP'.\n\n"
                    f"We will update only the modified fields."
                )
            else:
                text = (
                    f"🏢 *Register Your MSME Business Details*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"The fastest way:\n"
                    f"📸 Simply send a photo of your *Udyam Certificate*, *GST Certificate*, or *Visiting Card*.\n\n"
                    f"Or send a quick Voice Note:\n"
                    f"🎙️ 'My shop name is Sri Balaji Textiles, Owner Balaji, Guntur, GST 36...'\n\n"
                    f"Or type the details directly."
                )

        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.TEXT,
            text_content=text,
        )

    def _build_missing_business_name_prompt(self, session: OnboardingSession) -> OutboundMessage:
        lang = session.preferred_language
        if lang == "ta":
            text = (
                "🎙️ உங்கள் பதிவு பெறப்பட்டது, ஆனால் வணிகம்/கடையின் பெயர் தெளிவாகத் தெரியவில்லை.\n\n"
                "தயவுசெய்து உங்கள் கடையின் பெயரை மட்டும் குரல் குறிப்பு அல்லது செய்தியாக அனுப்பவும்:\n"
                "உதா: 'என் கடையின் பெயர் ஸ்ரீ பாலாஜி டெக்ஸ்டைல்ஸ்'."
            )
        elif lang == "te":
            text = (
                "🎙️ మీ సందేశం అందింది, కానీ దుకాణం పేరు స్పష్టంగా అర్థం కాలేదు.\n\n"
                "దయచేసి మీ షాపు లేదా వ్యాపార సంస్థ పేరును వాయిస్ లేదా టెక్స్ట్ ద్వారా చెప్పండి:\n"
                "ఉదా: 'నా షాపు పేరు శ్రీ బాలాజీ టెక్స్‌టైల్స్'."
            )
        elif lang == "hi":
            text = (
                "🎙️ आपका संदेश मिल गया है, लेकिन दुकान का नाम स्पष्ट नहीं हो सका।\n\n"
                "कृपया अपनी दुकान या व्यापार का नाम बोलकर या लिखकर भेजें:\n"
                "उदा: 'मेरी दुकान का नाम श्री बालाजी टेक्सटाइल्स'."
            )
        else:
            text = (
                "🎙️ Message received, but we couldn't catch the business name clearly.\n\n"
                "Please reply with just your shop, firm, or business name:\n"
                "Example: 'My shop name is Sri Balaji Textiles'."
            )
        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.TEXT,
            text_content=text,
        )

    # =========================================================================
    # Multi-Modal MSME Intake & Validation
    # =========================================================================

    async def _extract_from_audio(self, media_bytes: bytes, lang: str = "te") -> Dict[str, Any]:
        """
        Transcribes and extracts MSME onboarding details from vernacular voice notes using Gemini.
        Auto-detects spoken language (e.g. ta, te, hi, kn, en).
        """
        from google.genai.errors import APIError

        try:
            from app.utils.client import get_genai_client
            from google.genai import types
            client = get_genai_client()
            prompt = (
                "You are an MSME onboarding specialist for Indian merchants.\n"
                "The user is speaking in an Indian language (Telugu, Hindi, Tamil, Kannada, Gujarati, Marathi, Bengali, Malayalam, Punjabi, or English).\n"
                "Listen to the audio note and extract the following JSON fields:\n"
                "- detected_language: 2-letter language code of the spoken audio ('ta', 'te', 'hi', 'kn', 'gu', 'mr', 'bn', 'ml', 'pa', 'en')\n"
                "- business_name: Exact name of the shop / firm / business spoken\n"
                "- owner_name: Name of the owner / proprietor if spoken\n"
                "- address: City, town, district, or state if spoken\n"
                "- gstin: 15-character GSTIN if spoken\n"
                "- udyam_reg_no: Udyam Registration Number if spoken\n"
                "- pincode: 6-digit postal code if spoken\n"
                "- upi_vpa: UPI ID if spoken\n\n"
                "CRITICAL INSTRUCTIONS:\n"
                "1. If any field is not clearly spoken, set it to null. NEVER make up, invent, or guess details.\n"
                "2. Accurately detect the spoken language in detected_language.\n"
                "Return JSON only."
            )
            res = client.models.generate_content(
                model=settings.EXTRACTOR_MODEL or "gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(data=media_bytes, mime_type="audio/ogg"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MSMEExtraction,
                    temperature=0.1,
                ),
            )
            return self._decode_extraction_response(res.text)
        except (APIError, ValidationError) as e:
            logger.warning("Gemini audio extraction failed (%s)", type(e).__name__)

        return {}

    async def _extract_from_image(self, media_bytes: bytes, mime_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Extracts MSME details from an uploaded visiting card, GST cert, or Udyam cert photo using Gemini multimodal.
        """
        from google.genai.errors import APIError

        try:
            from app.utils.client import get_genai_client
            from google.genai import types
            client = get_genai_client()
            mime = (mime_type or "image/jpeg").split(";")[0].strip().lower()
            prompt = (
                "You are an MSME document reader for Indian business onboarding.\n"
                "Extract the following business fields in JSON from this image (Visiting Card, GSTIN Certificate, or Udyam Certificate):\n"
                "- business_name: Business / Shop / Firm name\n"
                "- owner_name: Name of owner / proprietor\n"
                "- gstin: 15-character Indian GSTIN\n"
                "- udyam_reg_no: Udyam registration number\n"
                "- address: City, town, state, or full address\n"
                "- pincode: 6-digit postal code\n"
                "- upi_vpa: UPI VPA / QR payment ID\n"
                "Set unstated fields to null. Return JSON only."
            )
            res = client.models.generate_content(
                model=settings.EXTRACTOR_MODEL or "gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(data=media_bytes, mime_type=mime),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MSMEExtraction,
                    temperature=0.1,
                ),
            )
            return self._decode_extraction_response(res.text)
        except (APIError, ValidationError) as e:
            logger.warning("Gemini image OCR extraction failed (%s)", type(e).__name__)

        return {}

    @staticmethod
    def _decode_extraction_response(response_text: Optional[str]) -> Dict[str, Any]:
        if not response_text:
            return {}
        return MSMEExtraction.model_validate_json(response_text).model_dump(exclude_none=True)

    async def _extract_from_text_with_gemini(self, text: str) -> Dict[str, Any]:
        """Extract natural-language details without treating filler as part of a name."""
        from google.genai.errors import APIError

        try:
            from app.utils.client import get_genai_client
            from google.genai import types

            prompt = (
                "Extract Indian small-business onboarding details from the user's message. "
                "Return only the fields supported by the message; use null for everything else. "
                "Do not guess, expand abbreviations, or include phrases such as 'name is' in a business name. "
                "Keep locations separate from business_name. For example, "
                "'shop name is Sivasai Textiles in Guntur' means business_name='Sivasai Textiles' "
                "and address='Guntur, Andhra Pradesh'."
            )
            response = get_genai_client().models.generate_content(
                model=settings.EXTRACTOR_MODEL or "gemini-3.8-flash",
                contents=[prompt, text],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MSMEExtraction,
                    temperature=0.1,
                ),
            )
            return self._decode_extraction_response(response.text)
        except (APIError, ValidationError) as e:
            logger.warning("Gemini text extraction failed (%s)", type(e).__name__)
            return {}

    async def _process_msme_intake(
        self,
        session: OnboardingSession,
        message: InboundMessage,
        media_bytes: Optional[bytes] = None,
    ) -> OutboundMessage:
        """
        Parses MSME details from Document/Image OCR, Voice Note, or Text.
        Validates deterministically and renders confirmation card.
        """
        extracted = {}

        # 1. Structured WhatsApp Flows JSON Payload
        if message.text_content and message.text_content.startswith("{"):
            try:
                flow_data = json.loads(message.text_content)
                if isinstance(flow_data, dict):
                    extracted = {
                        "business_name": flow_data.get("business_name") or flow_data.get("trade_name"),
                        "owner_name": flow_data.get("owner_name"),
                        "gstin": flow_data.get("gstin"),
                        "udyam_reg_no": flow_data.get("udyam_reg_no") or flow_data.get("udyam"),
                        "address": flow_data.get("address"),
                        "pincode": flow_data.get("pincode"),
                        "upi_vpa": flow_data.get("upi_vpa") or flow_data.get("upi"),
                    }
            except json.JSONDecodeError:
                logger.info("Ignoring malformed WhatsApp Flow JSON onboarding payload")

        # 2. Explicitly labelled text fields and natural-language extraction
        if not extracted and message.text_content:
            text = message.text_content
            extracted = self._extract_from_text(text)
            if not extracted.get("business_name"):
                model_extracted = await self._extract_from_text_with_gemini(text)
                if model_extracted.get("business_name"):
                    model_extracted["business_name"] = self._normalize_business_name(
                        model_extracted["business_name"],
                        model_extracted,
                    )
                extracted.update({key: value for key, value in model_extracted.items() if value})

        # 3. Audio Voice Note
        if not extracted and message.message_type == MessageType.AUDIO and media_bytes:
            extracted = await self._extract_from_audio(media_bytes, session.preferred_language)
            if extracted.get("detected_language"):
                det_lang = str(extracted["detected_language"]).lower().strip()
                if det_lang in SUPPORTED_LANGUAGES:
                    session.preferred_language = det_lang
                    session.script_preference = SUPPORTED_LANGUAGES[det_lang].get("default_script", "Latin")

        # 4. Media (OCR for Images/Documents)
        if not extracted and message.message_type in (MessageType.IMAGE, MessageType.DOCUMENT):
            if media_bytes:
                extracted = await self._extract_from_image(media_bytes, message.media_mime_type)

        # Apply extracted fields to session (preserving previously collected fields if not in new extraction)
        if extracted.get("business_name"):
            session.business_name = extracted["business_name"].strip()
        if extracted.get("owner_name"):
            session.owner_name = extracted["owner_name"].strip()
        if extracted.get("gstin"):
            session.gstin = extracted["gstin"].strip()
        if extracted.get("udyam_reg_no"):
            session.udyam_reg_no = extracted["udyam_reg_no"].strip()
        if extracted.get("pan"):
            session.pan_number = extracted["pan"].strip()
        if extracted.get("address"):
            session.business_address = extracted["address"].strip()
        if extracted.get("pincode"):
            session.pincode = extracted["pincode"].strip()
        if extracted.get("upi_vpa"):
            session.upi_vpa = extracted["upi_vpa"].strip()

        if session.editing and not extracted:
            return self._build_edit_retry_prompt(session)

        # If business name could not be identified, politely prompt for it in vernacular
        if not session.business_name:
            session.step = OnboardingStep.AWAITING_MSME_DETAILS
            return self._build_missing_business_name_prompt(session)

        # Deterministic Validation
        validation_errors = []
        gstin_valid_meta = None
        udyam_valid_meta = None

        if session.gstin:
            gst_res = validate_gstin(session.gstin, strict_checksum=False)
            if not gst_res.is_valid:
                validation_errors.append(f"GSTIN: {gst_res.error}")
            else:
                session.gstin = gst_res.value
                gstin_valid_meta = gst_res.metadata

        if session.udyam_reg_no:
            udyam_res = validate_udyam(session.udyam_reg_no)
            if not udyam_res.is_valid:
                validation_errors.append(f"Udyam: {udyam_res.error}")
            else:
                session.udyam_reg_no = udyam_res.value
                udyam_valid_meta = udyam_res.metadata

        if session.pincode:
            pin_res = validate_pincode(session.pincode)
            if pin_res.is_valid and not session.business_address:
                session.business_address = pin_res.metadata.get("state_name")

        if session.upi_vpa:
            upi_res = validate_upi_vpa(session.upi_vpa)
            if not upi_res.is_valid:
                validation_errors.append(f"UPI ID: {upi_res.error}")

        # Transition to Confirmation
        session.step = OnboardingStep.AWAITING_CONFIRMATION
        session.editing = False
        return self._build_confirmation_card(session, validation_errors)

    def _extract_from_text(self, text: str) -> Dict[str, Any]:
        result = {}

        # GSTIN pattern
        gst_match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", text, re.I)
        if gst_match:
            result["gstin"] = gst_match.group(1).upper()

        # Udyam pattern
        udyam_match = re.search(r"\b(UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7})\b", text, re.I)
        if udyam_match:
            result["udyam_reg_no"] = udyam_match.group(1).upper()

        # PAN pattern
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b", text, re.I)
        if pan_match and not result.get("gstin"):
            result["pan"] = pan_match.group(1).upper()

        # UPI pattern
        upi_match = re.search(r"\b([a-zA-Z0-9.\-_]{2,64}@[a-zA-Z]{2,32})\b", text)
        if upi_match:
            result["upi_vpa"] = upi_match.group(1).lower()

        # Pincode pattern
        pin_match = re.search(r"\b([1-9][0-9]{5})\b", text)
        if pin_match:
            result["pincode"] = pin_match.group(1)

        # Names extraction
        biz_match = re.search(
            r"(?:business(?:\s+name)?|shop(?:\s+name)?|dukaan|dukan|peru|name|store)\s*[:=]\s*([^,\n]+)",
            text,
            re.I,
        )
        if biz_match:
            result["business_name"] = biz_match.group(1).strip()

        owner_match = re.search(r"(?:owner|malik|proprietor)\s*[:=]\s*([^,\n]+)", text, re.I)
        if owner_match:
            result["owner_name"] = owner_match.group(1).strip()

        addr_match = re.search(r"(?:address|pata|chiru|location)\s*[:=]\s*([^,\n]+)", text, re.I)
        if addr_match:
            result["address"] = addr_match.group(1).strip()
        else:
            city_match = re.search(r"\b(guntur|dharmavaram|sircilla|surat|coimbatore|madurai|hyderabad|vijayawada|varanasi|jaipur|ludhiana|tiruppur)\b", text, re.I)
            if city_match:
                city = city_match.group(1).title()
                result["address"] = _CITY_ADDRESS_MAP.get(city, f"{city}, India")

        return result

    @staticmethod
    def _normalize_business_name(value: str, extracted: Dict[str, Any]) -> str:
        name = re.sub(
            r"^(?:(?:my|our|the)\s+)?(?:shop(?:\s+name)?|business(?:\s+name)?|store|name)\s+(?:is|:)\s*",
            "",
            value.strip(),
            flags=re.I,
        )
        location_match = re.search(
            r"\s+in\s+(guntur|dharmavaram|sircilla|surat|coimbatore|madurai|hyderabad|"
            r"vijayawada|varanasi|jaipur|ludhiana|tiruppur)\s*$",
            name,
            re.I,
        )
        if location_match:
            city = location_match.group(1).title()
            extracted.setdefault("address", _CITY_ADDRESS_MAP.get(city, f"{city}, India"))
            name = name[: location_match.start()]
        return name.strip()

    def _build_confirmation_card(
        self,
        session: OnboardingSession,
        errors: List[str],
    ) -> OutboundMessage:
        session.confirmation_nonce = uuid4().hex[:16]
        lang = session.preferred_language
        biz = session.business_name or "Not provided"
        owner = session.owner_name or "Proprietor"
        gst = session.gstin or "Unregistered (Non-GST MSME)"
        udyam = session.udyam_reg_no or "Not provided"
        addr = session.business_address or "Telangana / India"
        upi = session.upi_vpa or "None (Cash / Ledger)"

        if lang == "te":
            header = "🏢 వ్యాపార వివరాల నిర్ధారణ"
            body = (
                f"దయచేసి మీ MSME వివరాలు సరిచూసుకోండి:\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *దుకాణం పేరు:* {biz}\n"
                f"👤 *యజమాని:* {owner}\n"
                f"📞 *నమోదిత ఫోన్:* {session.phone}\n"
                f"📜 *Udyam నెంబర్:* {udyam}\n"
                f"🧾 *GSTIN:* {gst}\n"
                f"📍 *చిరునామా:* {addr}\n"
                f"💳 *UPI ID:* {upi}\n"
                f"🌐 *భాష:* తెలుగు\n\n"
                f"ఈ వివరాలు సరైనవేనా?"
            )
            btn_confirm = "సరే, ప్రారంభించు ✅"[:20]
            btn_edit = "మార్చు ✏️"[:20]
        elif lang == "hi":
            header = "🏢 व्यापार विवरण की पुष्टि"
            body = (
                f"कृपया अपने MSME व्यापार का विवरण जांचें:\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *दुकान/फर्म का नाम:* {biz}\n"
                f"👤 *मालिक:* {owner}\n"
                f"📞 *पंजीकृत नंबर:* {session.phone}\n"
                f"📜 *Udyam नंबर:* {udyam}\n"
                f"🧾 *GSTIN:* {gst}\n"
                f"📍 *पता:* {addr}\n"
                f"💳 *UPI ID:* {upi}\n"
                f"🌐 *भाषा:* हिंदी\n\n"
                f"क्या ये विवरण सही हैं?"
            )
            btn_confirm = "पुष्टि करें ✅"[:20]
            btn_edit = "संशोधित करें ✏️"[:20]
        else:
            header = "🏢 Confirm MSME Registration"
            body = (
                f"Please review your business details:\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *Business Name:* {biz}\n"
                f"👤 *Owner:* {owner}\n"
                f"📞 *Registered Phone:* {session.phone}\n"
                f"📜 *Udyam No:* {udyam}\n"
                f"🧾 *GSTIN:* {gst}\n"
                f"📍 *Address:* {addr}\n"
                f"💳 *UPI ID:* {upi}\n"
                f"🌐 *Language:* English\n\n"
                f"Are these details correct?"
            )
            btn_confirm = "Confirm & Start ✅"[:20]
            btn_edit = "Edit Details ✏️"[:20]

        if errors:
            body += f"\n\n⚠️ *Format Note:* {'; '.join(errors)}"

        buttons = [
            CanonicalButton(id=f"MSME_ONBOARD_CONFIRM_{session.confirmation_nonce}", title=btn_confirm[:20]),
            CanonicalButton(id=f"MSME_ONBOARD_EDIT_{session.confirmation_nonce}", title=btn_edit[:20]),
        ]

        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_header=header,
            interactive_body=body,
            interactive_footer="Clandexis Invisible ERP",
            buttons=buttons,
        )

    # =========================================================================
    # Final Database Provisioning & Welcome Celebration
    # =========================================================================

    async def _commit_merchant_onboarding(self, session: OnboardingSession) -> OutboundMessage:
        """
        Commits the validated MSME merchant into PostgreSQL (or fallback store)
        with is_approved = true.
        """
        merchant_id = str(uuid4())
        clean_phone = session.phone
        clean_biz = session.business_name or f"Shop {clean_phone}"
        clean_owner = session.owner_name or "Merchant"
        clean_lang = session.preferred_language
        clean_script = session.script_preference

        # Durable production onboarding commits the merchant and message receipt atomically
        # in PostgresOnboardingStore.finish. Preserve the legacy direct path for in-memory
        # callers, but never report activation if its database write fails.
        if self.store is None and settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
            import psycopg

            with psycopg.connect(settings.DATABASE_URL, connect_timeout=5) as conn:
                inserted = conn.execute(
                    """
                    INSERT INTO merchants (
                        id, phone_e164, is_approved, business_name, owner_name,
                        preferred_language, script_preference, tax_registration_number, tax_address
                    ) VALUES (%s, %s, true, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (phone_e164) DO NOTHING
                    RETURNING id
                    """,
                    (
                        merchant_id,
                        clean_phone,
                        clean_biz,
                        clean_owner,
                        clean_lang,
                        clean_script,
                        session.gstin,
                        json.dumps(
                            {
                                "gstin": session.gstin,
                                "udyam": session.udyam_reg_no,
                                "address": session.business_address,
                                "pincode": session.pincode,
                                "upi_vpa": session.upi_vpa,
                            }
                        ),
                    ),
                ).fetchone()
                if inserted is None:
                    raise RuntimeError("A merchant account already exists for this phone")
            logger.info("Committed merchant onboarding to PostgreSQL")

        session.step = OnboardingStep.COMPLETED

        # 2. Vernacular Celebration & First-Transaction Tutorial
        lang = session.preferred_language
        if lang == "te":
            text = (
                f"🎉 *అభినందనలు! మీ Clandexis Invisible ERP ఖాతా యాక్టివేట్ అయింది.*\n\n"
                f"మీ వ్యాపారం: *{clean_biz}*\n"
                f"మీరు ఇప్పుడు సులభంగా మీ అమ్మకాలు, నగదు మరియు ఉధార్ లెడ్జర్‌ను నిర్వహించవచ్చు.\n\n"
                f"🚀 *మీ మొదటి లావాదేవీని ఇప్పుడే నమోదు చేయండి:*\n"
                f"వాయిస్ లేదా టెక్స్ట్ మెసేజ్ పంపండి:\n"
                f"👉 *'రమేష్ కి 10 గద్వాల్ చీరలు రూ. 15,000 కి అమ్మాను, 5000 క్యాష్ వచ్చింది, 10000 బాకీ'* \n\n"
                f"మేము ఆటోమేటిక్‌గా బిల్లు తయారు చేసి లెడ్జర్‌లో నమోదు చేస్తాము!"
            )
        elif lang == "hi":
            text = (
                f"🎉 *बधाई हो! आपका Clandexis Invisible ERP खाता सक्रिय हो गया है।*\n\n"
                f"फर्म का नाम: *{clean_biz}*\n"
                f"अब आप बिना किसी परेशानी के अपनी बिक्री, नकद और उधारी का हिसाब रख सकते हैं।\n\n"
                f"🚀 *अपनी पहली एंट्री अभी करें:*\n"
                f"बोलकर या लिखकर भेजें:\n"
                f"👉 *'राजू को 10 साड़ियां 15000 में बेचीं, 5000 नकद मिला, 10000 उधार'* \n\n"
                f"हम तुरंत लेजर में दर्ज करके इनवॉइस तैयार कर देंगे!"
            )
        elif lang == "ta":
            text = (
                f"🎉 *வாழ்த்துகள்! உங்கள் Clandexis Invisible ERP கணக்கு தொடங்கப்பட்டது.*\n\n"
                f"வணிகம்: *{clean_biz}*\n"
                f"இனி உங்கள் விற்பனை, வரவு-செலவு மற்றும் கடன் கணக்குகளை எளிதாக நிர்வகிக்கலாம்.\n\n"
                f"🚀 *உங்கள் முதல் பதிவை இப்போது செய்யுங்கள்:*\n"
                f"குரல் குறிப்பு அல்லது செய்தி அனுப்பவும்:\n"
                f"👉 *'ராமுக்கு 10 சேலைகள் ரூ. 15,000-க்கு விற்றேன், 5000 ரொக்கம் வந்தது, 10000 பாக்கி'* \n\n"
                f"நாங்கள் உடனே கணக்கில் பதிவு செய்து இன்வாய்ஸ் உருவாக்கி விடுவோம்!"
            )
        else:
            text = (
                f"🎉 *Congratulations! Your Clandexis Invisible ERP account is now ACTIVE.*\n\n"
                f"Business: *{clean_biz}*\n"
                f"You can now manage your stock, receivables, and customer khata seamlessly.\n\n"
                f"🚀 *Try your first entry right now:*\n"
                f"Send a text or voice note like:\n"
                f"👉 *'Sold 10 silk sarees to Raju for Rs 15000, received 5000 cash, 10000 on credit'* \n\n"
                f"We will atomically post it to your ledger and generate your bill!"
            )

        return OutboundMessage(
            recipient_phone=clean_phone,
            message_type=MessageType.TEXT,
            text_content=text,
        )

    def _get_already_completed_message(self, session: OnboardingSession) -> str:
        lang = session.preferred_language
        if lang == "te":
            if not session.business_name:
                return "మీ Clandexis ఖాతా ఇప్పటికే యాక్టివ్‌లో ఉంది. లావాదేవీ వివరాలను కొత్త సందేశంగా పంపండి."
            return (
                f"మీ వ్యాపారం *{session.business_name}* ఇప్పటికే యాక్టివేట్ అయింది. "
                f"అమ్మకాలు నమోదు చేయడానికి మెసేజ్ లేదా వాయిస్ నోట్ పంపండి."
            )
        elif lang == "hi":
            if not session.business_name:
                return "आपका Clandexis खाता पहले से सक्रिय है। लेन-देन का विवरण नए संदेश के रूप में भेजें।"
            return (
                f"आपका व्यापार *{session.business_name}* पहले से सक्रिय है। "
                f"बिक्री दर्ज करने के लिए विवरण भेजें।"
            )
        if not session.business_name:
            return "Your Clandexis account is already active. Send transaction details as a new message."
        return (
            f"Your business *{session.business_name}* is already active. "
            f"Simply send your transaction details to post."
        )

    def _merchant_is_registered(self, phone: str) -> bool:
        if self.store is None or settings.DATABASE_BACKEND != "postgres" or not settings.DATABASE_URL:
            return False
        from app.services.whatsapp_identity import resolve_phone

        return bool(resolve_phone(phone))

    def _build_duplicate_message(self, session: OnboardingSession) -> OutboundMessage:
        if session.step == OnboardingStep.COMPLETED:
            return self._build_stale_onboarding_action_message(session, account_active=True)
        return OutboundMessage(
            recipient_phone=session.phone,
            text_content="This onboarding message was already processed. Send any new details as a new message.",
        )

    @staticmethod
    def _build_busy_message(session: OnboardingSession) -> OutboundMessage:
        return OutboundMessage(
            recipient_phone=session.phone,
            text_content="Your previous onboarding message is still being processed. Please try again shortly.",
        )

    def _build_stale_onboarding_action_message(
        self,
        session: OnboardingSession,
        *,
        account_active: bool = False,
    ) -> OutboundMessage:
        if account_active:
            if session.preferred_language == "te":
                text = "మీ ఖాతా ఇప్పటికే యాక్టివ్‌లో ఉంది. ఈ పాత నమోదు బటన్‌తో లావాదేవీ నమోదు చేయబడలేదు."
            elif session.preferred_language == "hi":
                text = "आपका खाता पहले से सक्रिय है। इस पुराने पंजीकरण बटन से कोई लेन-देन दर्ज नहीं हुआ।"
            else:
                text = "Your account is already active. This old registration button did not create a transaction."
        else:
            if session.preferred_language == "te":
                text = "ఈ నమోదు బటన్ గడువు ముగిసింది. కొత్త నిర్ధారణ కార్డు కోసం వివరాలను మళ్లీ పంపండి."
            elif session.preferred_language == "hi":
                text = "यह पंजीकरण बटन अब मान्य नहीं है। नया पुष्टि कार्ड पाने के लिए विवरण फिर से भेजें।"
            else:
                text = "This registration button is no longer valid. Send your details again to get a fresh confirmation card."
        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.TEXT,
            text_content=text,
        )

    @staticmethod
    def _build_edit_retry_prompt(session: OnboardingSession) -> OutboundMessage:
        text = (
            "I couldn't identify a business detail to update. Please send the field and its new value, "
            "for example: Business name: Sivasai Textiles."
        )
        return OutboundMessage(
            recipient_phone=session.phone,
            message_type=MessageType.TEXT,
            text_content=text,
        )
