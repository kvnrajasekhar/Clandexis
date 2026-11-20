"""Small language/script preferences and deterministic missing-field questions."""
import re


def preference(text, previous=None):
    previous = previous or {}
    if re.search(r"[\u0c00-\u0c7f]", text):
        return "te", "Telugu"
    if re.search(r"[\u0900-\u097f]", text):
        return "hi", "Devanagari"
    if re.search(r"\b(nenu|chesanu|chesa|ammnu|ammanu|ammaru|kavali|entha|teesukunna)\b", text, re.I):
        return "te", "Latin"
    if re.search(r"\b(maine|becha|bechi|kitna|hai|chahiye)\b", text, re.I):
        return "hi", "Latin"
    if re.search(r"\b(I|sold|received|today|please)\b", text, re.I):
        return "en", "Latin"
    return previous.get("detected_language", "en"), previous.get("script_preference", "Latin")


def question(candidate, language, script):
    missing = list(candidate.missing_fields)
    if candidate.transaction_type in ("sale", "purchase"):
        if not candidate.items or any(i.unit_price is None for i in candidate.items):
            missing = ["unit_price"] + [f for f in missing if f != "unit_price"]
        elif not candidate.payments and candidate.credit_amount is None:
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
    return candidate.clarification_question or "Please review the unresolved details in the dashboard.", missing
