"""Deterministic multilingual language/script preferences, onboarding options, and localized clarification questions."""
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_LANGUAGES: Dict[str, Dict[str, str]] = {
    "te": {"name": "Telugu", "native": "తెలుగు", "region": "ఆంధ్రప్రదేశ్ & తెలంగాణ", "default_script": "Telugu"},
    "hi": {"name": "Hindi", "native": "हिंदी", "region": "भारत", "default_script": "Devanagari"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "region": "தமிழ்நாடு", "default_script": "Tamil"},
    "kn": {"name": "Kannada", "native": "ಕನ್ನಡ", "region": "ಕರ್ನಾಟಕ", "default_script": "Kannada"},
    "gu": {"name": "Gujarati", "native": "ગુજરાતી", "region": "ગુજરાત", "default_script": "Gujarati"},
    "mr": {"name": "Marathi", "native": "मराठी", "region": "महाराष्ट्र", "default_script": "Devanagari"},
    "bn": {"name": "Bengali", "native": "বাংলা", "region": "পশ্চিমবঙ্গ", "default_script": "Bengali"},
    "ml": {"name": "Malayalam", "native": "മലയാളം", "region": "കേരളം", "default_script": "Malayalam"},
    "pa": {"name": "Punjabi", "native": "ਪੰਜਾਬੀ", "region": "ਪੰਜਾਬ", "default_script": "Gurmukhi"},
    "en": {"name": "English", "native": "English", "region": "India 🇮🇳", "default_script": "Latin"},
}


def preference(text: str, previous: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Detects language and script from text or falls back to previous conversation preference.
    """
    previous = previous or {}
    if not text:
        return previous.get("detected_language", "en"), previous.get("script_preference", "Latin")

    # Script regexes
    if re.search(r"[\u0c00-\u0c7f]", text):
        return "te", "Telugu"
    if re.search(r"[\u0b80-\u0bff]", text):
        return "ta", "Tamil"
    if re.search(r"[\u0c80-\u0cff]", text):
        return "kn", "Kannada"
    if re.search(r"[\u0a80-\u0aff]", text):
        return "gu", "Gujarati"
    if re.search(r"[\u0980-\u09ff]", text):
        return "bn", "Bengali"
    if re.search(r"[\u0d00-\u0d7f]", text):
        return "ml", "Malayalam"
    if re.search(r"[\u0a00-\u0a7f]", text):
        return "pa", "Gurmukhi"
    if re.search(r"[\u0900-\u097f]", text):
        return "hi", "Devanagari"

    # Vernacular greetings & common words transliterated in Latin
    if re.search(r"\b(namaskaram|bagunnara|nenu|chesanu|chesa|ammnu|ammanu|ammaru|kavali|entha|teesukunna|sare|avunu)\b", text, re.I):
        return "te", "Latin"
    if re.search(r"\b(vanakkam|epdi|nallairukken|koduthen|vaanginen|sari|aama)\b", text, re.I):
        return "ta", "Latin"
    if re.search(r"\b(namaskara|hegidhdheera|kotte|thagonde|haudu)\b", text, re.I):
        return "kn", "Latin"
    if re.search(r"\b(kem cho|majama|vechyu|aapo|ha)\b", text, re.I):
        return "gu", "Latin"
    if re.search(r"\b(namaskar|kasa kay|dila|ghetla|ho)\b", text, re.I):
        return "mr", "Latin"
    if re.search(r"\b(namashkar|kemon achen|dilam|nilam|hya)\b", text, re.I):
        return "bn", "Latin"
    if re.search(r"\b(namaste|pranam|ram ram|maine|becha|bechi|kitna|hai|chahiye|shuru|theek|haan)\b", text, re.I):
        return "hi", "Latin"
    if re.search(r"\b(sat sri akaal|ki haal|vecheya|leya|haanji)\b", text, re.I):
        return "pa", "Latin"
    if re.search(r"\b(hi|hello|start|sold|received|today|please|register|setup)\b", text, re.I):
        return "en", "Latin"

    return previous.get("detected_language", "en"), previous.get("script_preference", "Latin")


def get_onboarding_language_list(suggested_lang: str = "en") -> Dict[str, Any]:
    """
    Generates Meta WhatsApp Interactive List payload for language onboarding.
    Presents up to 10 popular Indian commercial languages in a native bottom-sheet picker.
    No UK flag and no language mixing in English header.
    """
    rows = []
    for code, meta in SUPPORTED_LANGUAGES.items():
        rows.append({
            "id": f"LANG_{code.upper()}",
            "title": f"{meta['native']} ({meta['name']})",
            "description": meta["region"],
        })

    return {
        "header": "🌐 Language Selection",
        "body": (
            "Welcome to Clandexis Invisible ERP!\n"
            "Please select your preferred language for invoices, sales, and accounts:"
        ),
        "button_text": "Select Language 🔽",
        "sections": [
            {
                "title": "Commercial Languages",
                "rows": rows[:10],
            }
        ],
    }


def get_onboarding_language_options(suggested_lang: str = "en") -> Dict[str, Any]:
    """Generates Meta WhatsApp Interactive button payload for quick 1-tap language choice.
    Presents up to 3 quick buttons (e.g. suggested vernacular + secondary + English).
    """
    if suggested_lang == "te":
        header = "🌐 భాషను ఎంచుకోండి / Language Setup"
        body = (
            "నమస్కారం! Clandexis Invisible ERP కి స్వాగతం.\n"
            "మేము మీతో తెలుగులో మాట్లాడగలము. మీరు ఏ భాషలో నిర్వహించాలనుకుంటున్నారు?"
        )
        buttons = [
            {"id": "LANG_TE", "title": "తెలుగు (Telugu) ✅"},
            {"id": "LANG_HI", "title": "हिंदी (Hindi)"},
            {"id": "LANG_EN", "title": "English"},
        ]
    elif suggested_lang == "hi":
        header = "🌐 भाषा का चयन / Language Setup"
        body = (
            "नमस्ते! Clandexis Invisible ERP में आपका स्वागत है।\n"
            "आप अपने व्यापार का हिसाब किस भाषा में रखना चाहते हैं?"
        )
        buttons = [
            {"id": "LANG_HI", "title": "हिंदी (Hindi) ✅"},
            {"id": "LANG_TE", "title": "తెలుగు (Telugu)"},
            {"id": "LANG_EN", "title": "English"},
        ]
    elif suggested_lang == "ta":
        header = "🌐 மொழியைத் தேர்ந்தெடுக்கவும் / Language Setup"
        body = (
            "வணக்கம்! Clandexis Invisible ERP-க்கு வரவேற்கிறோம்.\n"
            "உங்கள் வர்த்தகக் கணக்குகளை எந்த மொழியில் நிர்வகிக்க விரும்புகிறீர்கள்?"
        )
        buttons = [
            {"id": "LANG_TA", "title": "தமிழ் (Tamil) ✅"},
            {"id": "LANG_TE", "title": "తెలుగు (Telugu)"},
            {"id": "LANG_EN", "title": "English"},
        ]
    elif suggested_lang == "gu":
        header = "🌐 ભાષા પસંદ કરો / Language Setup"
        body = (
            "નમસ્તે! Clandexis Invisible ERP માં તમારું સ્વાગત છે.\n"
            "તમે તમારા વ્યાપારનું સંચાલન કઈ ભાષામાં કરવા માંગો છો?"
        )
        buttons = [
            {"id": "LANG_GU", "title": "ગુજરાતી (Gujarati) ✅"},
            {"id": "LANG_HI", "title": "हिंदी (Hindi)"},
            {"id": "LANG_EN", "title": "English"},
        ]
    else:
        header = "🌐 Select Language"
        body = (
            "Welcome to Clandexis Invisible ERP!\n"
            "Please choose your preferred language for invoices, sales, and accounts:"
        )
        buttons = [
            {"id": "LANG_EN", "title": "English"},
            {"id": "LANG_TE", "title": "తెలుగు (Telugu)"},
            {"id": "LANG_HI", "title": "हिंदी (Hindi)"},
        ]

    return {
        "header": header,
        "body": body,
        "buttons": buttons,
    }


def resolve_language_and_script(
    text: str | None = None,
    tx=None,
    previous: dict | None = None,
    is_audio: bool = False,
) -> tuple[str, str]:
    """Resolves language and script preference for incoming messages.

    For audio messages, uses Gemini's detected_language and raw_transcript to determine
    the spoken language directly, rather than relying on stale conversation history.
    Telugu voice defaults to Telugu script.
    Hindi voice defaults to Hindi Devanagari script.
    English voice defaults to English.
    """
    previous = previous or {}
    text = (text or "").strip()

    if is_audio and tx is not None:
        # Check raw_transcript first for native script markers
        transcript = (getattr(tx, "raw_transcript", None) or "").strip()
        if re.search(r"[\u0c00-\u0c7f]", transcript):
            return "te", "Telugu"
        if re.search(r"[\u0900-\u097f]", transcript):
            return "hi", "Devanagari"
        if re.search(r"[\u0a80-\u0aff]", transcript):
            return "gu", "Gujarati"
        if re.search(r"[\u0b80-\u0bff]", transcript):
            return "ta", "Tamil"
        if re.search(
            r"\b(nenu|chesanu|chesa|ammnu|ammanu|ammaru|kavali|entha|teesukunna|ammamu|ammadu|ammindi|vachindi|istaru|baaki|leka)\b",
            transcript,
            re.I,
        ):
            return "te", "Latin"
        if re.search(
            r"\b(maine|becha|bechi|kitna|hai|chahiye|diya|liya|kharida|kaafi|hoga)\b",
            transcript,
            re.I,
        ):
            return "hi", "Latin"

        # Check detected_language from Gemini
        det = (getattr(tx, "detected_language", None) or "").lower().strip()
        if "telugu" in det or det.startswith("te"):
            if "latn" in det or "roman" in det:
                return "te", "Latin"
            return "te", "Telugu"
        if "hindi" in det or det.startswith("hi"):
            if "latn" in det or "roman" in det:
                return "hi", "Latin"
            return "hi", "Devanagari"
        if "gujarati" in det or det.startswith("gu"):
            return "gu", "Gujarati"
        if "tamil" in det or det.startswith("ta"):
            return "ta", "Tamil"
        if "english" in det or det.startswith("en"):
            return "en", "Latin"

        if re.search(
            r"\b(I|sold|received|today|please|bought|purchase|credit|balance|shirt|shirts)\b",
            transcript,
            re.I,
        ):
            return "en", "Latin"

    # For text messages, check input text directly
    if text:
        det_lang, det_script = preference(text, previous)
        if det_lang != "en" or text.strip().lower() in ("hi", "hello", "start", "sold", "received", "today", "please"):
            # If the user previously had native script and gives a short reply in latin/digits, preserve native script
            if previous.get("script_preference") in ("Telugu", "Devanagari") and det_script == "Latin":
                if det_lang == previous.get("detected_language") or not re.search(r"[a-zA-Z]{4,}", text):
                    det_script = previous.get("script_preference", det_script)
            return det_lang, det_script

    # Also check if tx has detected_language even if not explicitly marked is_audio
    if tx is not None:
        det = (getattr(tx, "detected_language", None) or "").lower().strip()
        if "telugu" in det or det.startswith("te"):
            return ("te", "Latin") if ("latn" in det or "roman" in det) else ("te", "Telugu")
        if "hindi" in det or det.startswith("hi"):
            return ("hi", "Latin") if ("latn" in det or "roman" in det) else ("hi", "Devanagari")
        if "english" in det or det.startswith("en"):
            return "en", "Latin"

    # Ambiguous or short clarification replies preserve the active candidate language
    return previous.get("detected_language", "en"), previous.get("script_preference", "Latin")


def question(
    candidate: Any,
    language: str,
    script: str,
    warnings: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    missing = list(getattr(candidate, "missing_fields", []) or [])
    warnings = warnings or getattr(candidate, "validation_warnings", []) or []

    # 1. Missing item price
    has_missing_price = False
    if getattr(candidate, "transaction_type", None) in ("sale", "purchase"):
        items = getattr(candidate, "items", []) or []
        if not items or any(getattr(i, "unit_price", None) is None for i in items):
            has_missing_price = True

    # 2. Missing payment settlement
    has_missing_payment = False
    if getattr(candidate, "transaction_type", None) in ("sale", "purchase"):
        payments = getattr(candidate, "payments", []) or []
        credit_amount = getattr(candidate, "credit_amount", None)
        amount_paid = getattr(candidate, "amount_paid", None)
        if not payments and credit_amount is None and (amount_paid is None or amount_paid == 0):
            has_missing_payment = True

    # 3. Missing customer / buyer in sale
    has_missing_buyer = False
    if getattr(candidate, "transaction_type", None) == "sale" and not getattr(candidate, "buyer_name", None):
        has_missing_buyer = True

    # Sync missing field priorities
    if has_missing_price and "unit_price" not in missing:
        missing.insert(0, "unit_price")
    elif not has_missing_price and "unit_price" in missing:
        missing.remove("unit_price")

    if has_missing_payment and "payment_details" not in missing:
        missing.append("payment_details")
    elif not has_missing_payment and "payment_details" in missing:
        missing.remove("payment_details")

    if has_missing_buyer and "buyer_name" not in missing:
        missing.append("buyer_name")

    # Priority selection
    if has_missing_price:
        key = "price"
    elif has_missing_payment:
        key = "payment"
    elif has_missing_buyer:
        key = "buyer"
    elif any("stock" in str(w).lower() for w in warnings):
        key = "stock"
    elif any(
        "catalog" in str(w).lower() or "uncataloged" in str(w).lower() or "product" in str(w).lower()
        for w in warnings
    ):
        key = "catalog"
    elif any(
        "mismatch" in str(w).lower() or "arithmetic" in str(w).lower() or "differs" in str(w).lower()
        for w in warnings
    ):
        key = "arithmetic"
    elif missing and missing[0] == "unit_price":
        key = "price"
    elif missing and missing[0] == "payment_details":
        key = "payment"
    elif missing and missing[0] in ("buyer_name", "customer"):
        key = "buyer"
    else:
        key = "general"

    buyer = getattr(candidate, "buyer_name", None) or ""
    items = getattr(candidate, "items", []) or []
    first_item = items[0] if items else None
    item_name = getattr(first_item, "item_name", "item") if first_item else "item"
    qty_val = getattr(first_item, "quantity", None) if first_item else None
    if qty_val is not None:
        qty_str = str(int(qty_val)) if qty_val == int(qty_val) else str(qty_val)
    else:
        qty_str = ""
    item_desc = f"{qty_str} {item_name}".strip() if qty_str else item_name

    total_val = getattr(candidate, "total_amount", None)
    if total_val is not None:
        total_str = f"₹{Decimal(str(total_val)):,.2f}".rstrip("0").rstrip(".")
    else:
        total_str = ""

    prompts = {
        ("te", "Telugu"): {
            "price": (
                f"{buyer}కి {item_desc} అమ్మారు. ఒక్కో {item_name} ధర ఎంత?"
                if (buyer and first_item)
                else "ఒక్కో వస్తువు ధర ఎంత?"
            ),
            "payment": (
                f"మొత్తం {total_str}. ఎంత చెల్లించారు? నగదు, యూపీఐ లేదా బాకీనా?"
                if total_str
                else "ఎంత చెల్లించారు? నగదు, యూపీఐ లేదా బాకీనా?"
            ),
            "buyer": "ఈ లావాదేవీ ఏ కస్టమర్ లేదా పార్టీకి సంబంధించింది?",
            "stock": "ఈ అమ్మకానికి సరిపడా స్టాక్ లేదు. దయచేసి సరైన పరిమాణం తెలపండి.",
            "catalog": "ఈ వస్తువు కేటలాగ్‌లో కనుగొనబడలేదు. దయచేసి సరైన వస్తువు పేరు తెలపండి.",
            "arithmetic": "చెల్లింపు మరియు బాకీ మొత్తం ధరకు సరిపోలలేదు. దయచేసి వివరాలు సరిచూడండి.",
            "general": "లావాదేవీని పూర్తి చేయడానికి దయచేసి మిగిలిన వివరాలను (ధర, చెల్లింపు లేదా కస్టమర్) తెలపండి.",
        },
        ("te", "Latin"): {
            "price": (
                f"{buyer} ki {item_desc} ammaru. Okkokka {item_name} price entha?"
                if (buyer and first_item)
                else "Okkokka item price entha?"
            ),
            "payment": (
                f"Mottham {total_str}. Entha payment vachindi? Cash, UPI, leka udhaar aa?"
                if total_str
                else "Entha payment vachindi? Cash, UPI, leka udhaar aa?"
            ),
            "buyer": "Ee transaction ye customer leka party ki sambhandhinchindi?",
            "stock": "Ee sale ki saripoye stock ledu. Dayachesi saraina quantity telapandi.",
            "catalog": "Ee item catalog lo ledu. Dayachesi saraina item peru telapandi.",
            "arithmetic": "Payment mariyu baaki mottham dharaku saripoledhu. Dayachesi details sari chudandi.",
            "general": "Transaction poorthi cheyadaniki dayachesi migatha details (price, payment leda customer) telapandi.",
        },
        ("hi", "Devanagari"): {
            "price": (
                f"आपने {buyer} को {item_desc} बेची हैं। एक {item_name} की कीमत कितनी है?"
                if (buyer and first_item)
                else "हर वस्तु की कीमत कितनी है?"
            ),
            "payment": (
                f"कुल {total_str}। कितना भुगतान मिला? नकद, UPI या उधार?"
                if total_str
                else "कितना भुगतान मिला? नकद, UPI या उधार?"
            ),
            "buyer": "यह लेन-देन किस ग्राहक या पार्टी के नाम पर दर्ज करना है?",
            "stock": "इस बिक्री के लिए पर्याप्त स्टॉक नहीं है। कृपया सही मात्रा बताएं।",
            "catalog": "यह सामान कैटलॉग में नहीं मिला। कृपया सही सामान का नाम बताएं।",
            "arithmetic": "भुगतान और उधार की रकम कुल कीमत से मेल नहीं खाती। कृपया सही विवरण दें।",
            "general": "लेन-देन पूरा करने के लिए कृपया बाकी विवरण (कीमत, भुगतान या ग्राहक) बताएं।",
        },
        ("hi", "Latin"): {
            "price": (
                f"Aapne {buyer} ko {item_desc} bechi hain. Ek {item_name} ki keemat kitni hai?"
                if (buyer and first_item)
                else "Har item ki keemat kitni hai?"
            ),
            "payment": (
                f"Kul {total_str}. Kitna payment mila? Cash, UPI ya udhaar?"
                if total_str
                else "Kitna payment mila? Cash, UPI ya udhaar?"
            ),
            "buyer": "Yeh len-den kis customer ya party ke naam par darj karna hai?",
            "stock": "Is sale ke liye stock kaafi nahi hai. Kripya sahi quantity batayein.",
            "catalog": "Yeh item catalog mein nahi mila. Kripya sahi item ka naam batayein.",
            "arithmetic": "Payment aur udhaar ki rakam kul keemat se match nahi karti. Kripya sahi details dein.",
            "general": "Len-den poora karne ke liye kripya baaki details (keemat, payment ya customer) batayein.",
        },
        ("ta", "Tamil"): {
            "price": "ஒவ்வொரு பொருளின் விலை என்ன?",
            "payment": "எவ்வளவு பணம் பெறப்பட்டது? ரொக்கம், UPI அல்லது கடனா?",
            "buyer": "இது எந்த வாடிக்கையாளரின் பரிவர்த்தனை?",
            "stock": "போதுமான இருப்பு இல்லை.",
            "catalog": "பொருள் பட்டியலில் இல்லை.",
            "arithmetic": "தொகை பொருந்தவில்லை.",
            "general": "விவரங்களை தெளிவுபடுத்தவும்.",
        },
        ("kn", "Kannada"): {
            "price": "ಪ್ರತಿಯೊಂದು ವಸ್ತುವಿನ ಬೆಲೆ ಎಷ್ಟು?",
            "payment": "ಎಷ್ಟು ಪಾವತಿ ಬಂದಿದೆ? ನಗದು, UPI ಅಥವಾ ಉದಾರಿಯೇ?",
            "buyer": "ಇದು ಯಾವ ಗ್ರಾಹಕರ ವ್ಯವಹಾರ?",
            "stock": "ಸಾಕಷ್ಟು ದಾಸ್ತಾನು ಇಲ್ಲ.",
            "catalog": "ಐಟಂ ಕ್ಯಾಟಲಾಗ್‌ನಲ್ಲಿ ಕಂಡುಬಂದಿಲ್ಲ.",
            "arithmetic": "ಮೊತ್ತ ಹೊಂದಾಣಿಕೆಯಾಗುತ್ತಿಲ್ಲ.",
            "general": "ವಿವರಗಳನ್ನು ದೃಢೀಕರಿಸಿ.",
        },
        ("gu", "Gujarati"): {
            "price": "દરેક વસ્તુની કિંમત કેટલી છે?",
            "payment": "કેટલી ચુકવણી મળી? રોકડ, UPI કે ઉધાર?",
            "buyer": "આ કયા ગ્રાહકનો વ્યવહાર છે?",
            "stock": "પર્યાપ્ત સ્ટોક નથી.",
            "catalog": "આ વસ્તુ કૅટેલૉગમાં મળી નથી.",
            "arithmetic": "રકમ મેળ ખાતી નથી.",
            "general": "બાકી વિગતો જણાવો.",
        },
        ("mr", "Devanagari"): {
            "price": "प्रत्येक वस्तूची किंमत किती आहे?",
            "payment": "किती रक्कम मिळाली? रोख, UPI की उधारी?",
            "buyer": "हा व्यवहार कोणत्या ग्राहकाचा आहे?",
            "stock": "पुरेसा साठा उपलब्ध नाही.",
            "catalog": "ही वस्तू कॅटलॉगमध्ये सापडली नाही.",
            "arithmetic": "रक्कम जुळत नाही.",
            "general": "उर्वरित तपशील स्पष्ट करा.",
        },
        ("bn", "Bengali"): {
            "price": "প্রতিটি জিনিসের দাম কত?",
            "payment": "কত টাকা পাওয়া গেছে? নগদ, UPI নাকি বাকি?",
            "buyer": "এটি কোন গ্রাহকের লেনদেন?",
            "stock": "পর্যাপ্ত স্টক নেই।",
            "catalog": "এই পণ্য ক্যাটালগে নেই।",
            "arithmetic": "পরিমাণ মেলেনি।",
            "general": "বাকি বিবরণ দিন।",
        },
        ("en", "Latin"): {
            "price": (
                f"You sold {item_desc} to {buyer}. What is the price of each item?"
                if (buyer and first_item)
                else "What is the price of each item?"
            ),
            "payment": (
                f"Total {total_str}. How much was paid, by which method, and how much is on credit?"
                if total_str
                else "How much was paid, by which method, and how much is on credit?"
            ),
            "buyer": "Who is the customer or party for this transaction?",
            "stock": "Insufficient stock available for this sale. Please provide a corrected quantity.",
            "catalog": "This product was not found in your catalog. Please specify the correct item name.",
            "arithmetic": "Payment amounts and credit do not match the transaction total. Please verify the amounts.",
            "general": "Please clarify the missing transaction details (price, payment split, or customer name).",
        },
    }

    lang_dict = prompts.get((language, script)) or prompts.get((language, "Telugu")) or prompts.get((language, "Devanagari")) or prompts[("en", "Latin")]
    q_text = lang_dict.get(key, lang_dict.get("general", prompts[("en", "Latin")]["general"]))
    return q_text, missing
