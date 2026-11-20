"""
Test Suite for Multilingual Indian Language Selection and Prompts.
Verifies:
- 10 Indian commercial languages catalog (Telugu, Hindi, Tamil, Kannada, Gujarati, Marathi, Bengali, Malayalam, Punjabi, English).
- Script and greeting regex heuristics across Indian scripts.
- Absence of UK flag (🇬🇧) in English labels.
- Clean header without mixing Telugu into the default English fallback header.
- Interactive list payload structure complying with Meta 10-row limit.
- Vernacular clarification questions for missing fields.
"""

from app.services.conversation_language import (
    SUPPORTED_LANGUAGES,
    get_onboarding_language_list,
    get_onboarding_language_options,
    preference,
    question,
)


def test_10_indian_languages_supported():
    expected_codes = ["te", "hi", "ta", "kn", "gu", "mr", "bn", "ml", "pa", "en"]
    for code in expected_codes:
        assert code in SUPPORTED_LANGUAGES
        assert "name" in SUPPORTED_LANGUAGES[code]
        assert "native" in SUPPORTED_LANGUAGES[code]
        assert "region" in SUPPORTED_LANGUAGES[code]


def test_language_detection_heuristics():
    # Telugu script
    assert preference("నమస్కారం")[0] == "te"
    # Tamil script
    assert preference("வணக்கம்")[0] == "ta"
    # Kannada script
    assert preference("ನಮಸ್ಕಾರ")[0] == "kn"
    # Gujarati script
    assert preference("નમસ્તે")[0] == "gu"
    # Bengali script
    assert preference("নমস্কার")[0] == "bn"
    # Hindi / Devanagari script
    assert preference("नमस्ते")[0] == "hi"
    # Punjabi / Gurmukhi script
    assert preference("ਸਤਿ ਸ਼੍ਰੀ ਅਕਾਲ")[0] == "pa"
    # Transliterated Telugu
    assert preference("bagunnara")[0] == "te"
    # Transliterated Tamil
    assert preference("vanakkam")[0] == "ta"
    # Transliterated Hindi
    assert preference("ram ram bhai")[0] == "hi"


def test_onboarding_language_list_structure():
    list_payload = get_onboarding_language_list()
    assert list_payload["header"] == "🌐 Language Selection"
    assert "భాషను" not in list_payload["header"]  # Telugu removed from English header
    assert len(list_payload["sections"]) == 1
    rows = list_payload["sections"][0]["rows"]
    assert len(rows) == 10  # Meta 10-row limit

    # Verify no UK flag
    for row in rows:
        assert "🇬🇧" not in row["title"]
        assert "🇬🇧" not in (row.get("description") or "")


def test_onboarding_options_no_uk_flag():
    opts = get_onboarding_language_options("en")
    assert "భాషను" not in opts["header"]
    for btn in opts["buttons"]:
        assert "🇬🇧" not in btn["title"]


def test_vernacular_question_prompts():
    class DummyItem:
        unit_price = None

    class DummyCandidate:
        transaction_type = "sale"
        items = [DummyItem()]
        missing_fields = ["unit_price"]
        clarification_question = None

    candidate = DummyCandidate()

    q_te, missing = question(candidate, "te", "Telugu")
    assert "ఒక్కో వస్తువు ధర ఎంత?" in q_te
    assert "unit_price" in missing

    q_ta, _ = question(candidate, "ta", "Tamil")
    assert "விலை என்ன" in q_ta

    q_kn, _ = question(candidate, "kn", "Kannada")
    assert "ಬೆಲೆ ಎಷ್ಟು" in q_kn

    q_hi, _ = question(candidate, "hi", "Devanagari")
    assert "कीमत कितनी है" in q_hi

    q_en, _ = question(candidate, "en", "Latin")
    assert "price of each item" in q_en
