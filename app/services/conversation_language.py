"""Language/script preferences, vernacular prompts, and interactive language selectors."""
import re
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
    """
    Generates Meta WhatsApp Interactive button payload for quick 1-tap language choice.
    Removed UK flag and removed Telugu from English fallback header.
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


def question(candidate: Any, language: str, script: str) -> Tuple[str, List[str]]:
    missing = list(getattr(candidate, "missing_fields", []))
    if getattr(candidate, "transaction_type", None) in ("sale", "purchase"):
        items = getattr(candidate, "items", [])
        payments = getattr(candidate, "payments", [])
        credit_amount = getattr(candidate, "credit_amount", None)
        if not items or any(getattr(i, "unit_price", None) is None for i in items):
            missing = ["unit_price"] + [f for f in missing if f != "unit_price"]
        elif not payments and credit_amount is None:
            missing = ["payment_details"] + [f for f in missing if f != "payment_details"]
    key = "price" if missing and missing[0] == "unit_price" else "payment"
    prompts = {
        ("te", "Latin"): {"price": "Okkokka item price entha?", "payment": "Entha payment vachindi? Cash, UPI, leka udhaar aa?"},
        ("te", "Telugu"): {"price": "ఒక్కో వస్తువు ధర ఎంత?", "payment": "ఎంత చెల్లించారు? నగదు, యూపీఐ లేదా బాకీనా?"},
        ("hi", "Devanagari"): {"price": "हर वस्तु की कीमत कितनी है?", "payment": "कितना भुगतान मिला? नकद, UPI या उधार?"},
        ("hi", "Latin"): {"price": "Har item ki keemat kitni hai?", "payment": "Kitna payment mila? Cash, UPI ya udhaar?"},
        ("ta", "Tamil"): {"price": "ஒவ்வொரு பொருளின் விலை என்ன?", "payment": "எவ்வளவு பணம் பெறப்பட்டது? ரொக்கம், UPI அல்லது கடனா?"},
        ("kn", "Kannada"): {"price": "ಪ್ರತಿಯೊಂದು ವಸ್ತುವಿನ ಬೆಲೆ ಎಷ್ಟು?", "payment": "ಎಷ್ಟು ಪಾವತಿ ಬಂದಿದೆ? ನಗದು, UPI ಅಥವಾ ಉದಾರಿಯೇ?"},
        ("gu", "Gujarati"): {"price": "દરેક વસ્તુની કિંમત કેટલી છે?", "payment": "કેટલી ચુકવણી મળી? રોકડ, UPI કે ઉધાર?"},
        ("mr", "Devanagari"): {"price": "प्रत्येक वस्तूची किंमत किती आहे?", "payment": "किती रक्कम मिळाली? रोख, UPI की उधारी?"},
        ("bn", "Bengali"): {"price": "প্রতিটি জিনিসের দাম কত?", "payment": "কত টাকা পাওয়া গেছে? নগদ, UPI নাকি বাকি?"},
        ("en", "Latin"): {"price": "What is the price of each item?", "payment": "How much was paid, by which method, and how much is on credit?"},
    }
    if missing and missing[0] in ("unit_price", "payment_details"):
        prompt_dict = prompts.get((language, script)) or prompts.get((language, "Telugu")) or prompts.get((language, "Devanagari")) or prompts[("en", "Latin")]
        return prompt_dict.get(key, prompts[("en", "Latin")][key]), missing
    clarification = getattr(candidate, "clarification_question", None)
    return clarification or "Please review the unresolved details in the dashboard.", missing
