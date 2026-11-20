"""Language/script preferences, vernacular prompts, and interactive language selectors."""
import re
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_LANGUAGES = {
    "te": {"name": "Telugu", "native": "తెలుగు", "default_script": "Telugu"},
    "hi": {"name": "Hindi", "native": "हिंदी", "default_script": "Devanagari"},
    "en": {"name": "English", "native": "English", "default_script": "Latin"},
    "gu": {"name": "Gujarati", "native": "ગુજરાતી", "default_script": "Gujarati"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "default_script": "Tamil"},
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
    if re.search(r"[\u0900-\u097f]", text):
        return "hi", "Devanagari"
    if re.search(r"[\u0a80-\u0aff]", text):
        return "gu", "Gujarati"
    if re.search(r"[\u0b80-\u0bff]", text):
        return "ta", "Tamil"

    # Vernacular greetings & common words transliterated in Latin
    if re.search(r"\b(namaskaram|bagunnara|nenu|chesanu|chesa|ammnu|ammanu|ammaru|kavali|entha|teesukunna|sare|avunu)\b", text, re.I):
        return "te", "Latin"
    if re.search(r"\b(namaste|pranam|ram ram|maine|becha|bechi|kitna|hai|chahiye|shuru|theek)\b", text, re.I):
        return "hi", "Latin"
    if re.search(r"\b(kem cho|majama|vechyu|aapo)\b", text, re.I):
        return "gu", "Latin"
    if re.search(r"\b(vanakkam|epdi|nallairukken|koduthen)\b", text, re.I):
        return "ta", "Latin"
    if re.search(r"\b(hi|hello|start|sold|received|today|please|register|setup)\b", text, re.I):
        return "en", "Latin"

    return previous.get("detected_language", "en"), previous.get("script_preference", "Latin")


def get_onboarding_language_options(suggested_lang: str = "en") -> Dict[str, Any]:
    """
    Generates Meta WhatsApp Interactive button payload for language onboarding.
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
            {"id": "LANG_EN", "title": "English 🇬🇧"},
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
            {"id": "LANG_EN", "title": "English 🇬🇧"},
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
            {"id": "LANG_EN", "title": "English 🇬🇧"},
        ]
    else:
        header = "🌐 Select Language / భాషను ఎంచుకోండి"
        body = (
            "Welcome to Clandexis Invisible ERP!\n"
            "Please choose your preferred language for invoices, sales, and accounts:"
        )
        buttons = [
            {"id": "LANG_EN", "title": "English 🇬🇧"},
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
        ("en", "Latin"): {"price": "What is the price of each item?", "payment": "How much was paid, by which method, and how much is on credit?"},
    }
    if missing and missing[0] in ("unit_price", "payment_details"):
        return prompts.get((language, script), prompts[("en", "Latin")])[key], missing
    clarification = getattr(candidate, "clarification_question", None)
    return clarification or "Please review the unresolved details in the dashboard.", missing
