"""
Unit Tests for MSME Business Validation Service.
Verifies:
- GSTIN regex, state codes, internal PAN validation, and official Luhn Mod-36 checksum.
- Udyam Registration Number (URN) format and state code checks.
- PAN format and entity code classification (Proprietor vs Firm vs Company).
- Pincode formatting and vernacular language / state inference.
- UPI Virtual Payment Address (VPA) format.
"""

import pytest
from app.services.msme_validator import (
    compute_gstin_checksum,
    validate_gstin,
    validate_pan,
    validate_pincode,
    validate_udyam,
    validate_upi_vpa,
)


def test_gstin_checksum_algorithm():
    # Official GSTIN test vectors:
    # 27AAPFU0939F1ZV (Maharashtra)
    assert compute_gstin_checksum("27AAPFU0939F1Z") == "V"


def test_gstin_validation_valid():
    valid_gstin = "27AAPFU0939F1ZV"
    res = validate_gstin(valid_gstin, strict_checksum=True)
    assert res.is_valid is True
    assert res.value == "27AAPFU0939F1ZV"
    assert res.metadata["state_code"] == "27"
    assert res.metadata["state_name"] == "Maharashtra"
    assert res.metadata["pan"] == "AAPFU0939F"


def test_gstin_validation_invalid_checksum():
    # Tamper with the check digit
    tampered_gstin = "27AAPFU0939F1ZX"
    res = validate_gstin(tampered_gstin, strict_checksum=True)
    assert res.is_valid is False
    assert "Invalid GSTIN checksum" in (res.error or "")


def test_gstin_validation_invalid_state():
    bad_state_gstin = "99AAPFU0939F1ZV"
    res = validate_gstin(bad_state_gstin, strict_checksum=False)
    assert res.is_valid is False
    assert "Invalid state code" in (res.error or "")


def test_udyam_validation_valid():
    valid_urn = "UDYAM-TS-04-0019284"
    res = validate_udyam(valid_urn)
    assert res.is_valid is True
    assert res.value == "UDYAM-TS-04-0019284"
    assert res.metadata["state_code"] == "TS"
    assert res.metadata["district_code"] == "04"
    assert res.metadata["registration_number"] == "0019284"


def test_udyam_validation_invalid():
    res1 = validate_udyam("UDYAM-ZZ-00-1234567")
    assert res1.is_valid is False
    assert "Unknown state code" in (res1.error or "")

    res2 = validate_udyam("UDYAM-12345")
    assert res2.is_valid is False
    assert "Invalid Udyam format" in (res2.error or "")


def test_pan_validation_entity_classification():
    # Individual / Proprietor (4th char 'P')
    res_p = validate_pan("AAAPB1234M")
    assert res_p.is_valid is True
    assert "Individual / Proprietorship" in res_p.metadata["entity_type"]

    # Company / Corporation (4th char 'C')
    res_c = validate_pan("AAACB1234M")
    assert res_c.is_valid is True
    assert "Company / Corporation" in res_c.metadata["entity_type"]

    # Partnership Firm (4th char 'F')
    res_f = validate_pan("AAPFU0939F")
    assert res_f.is_valid is True
    assert "Partnership Firm" in res_f.metadata["entity_type"]

    # Invalid length/chars
    res_bad = validate_pan("12345")
    assert res_bad.is_valid is False


def test_pincode_state_and_language_inference():
    # Telangana (50xxxx) -> Telugu
    res_ts = validate_pincode("505301")
    assert res_ts.is_valid is True
    assert res_ts.metadata["state_name"] == "Telangana"
    assert res_ts.metadata["suggested_lang"] == "te"

    # Gujarat (38xxxx) -> Gujarati
    res_gj = validate_pincode("380001")
    assert res_gj.is_valid is True
    assert res_gj.metadata["state_name"] == "Gujarat"
    assert res_gj.metadata["suggested_lang"] == "gu"

    # Delhi (11xxxx) -> Hindi
    res_dl = validate_pincode("110001")
    assert res_dl.is_valid is True
    assert res_dl.metadata["state_name"] == "Delhi"
    assert res_dl.metadata["suggested_lang"] == "hi"

    # Invalid pincode
    res_bad = validate_pincode("01234")
    assert res_bad.is_valid is False


def test_upi_vpa_validation():
    assert validate_upi_vpa("srilakshmi@okicici").is_valid is True
    assert validate_upi_vpa("9876543210@paytm").is_valid is True
    assert validate_upi_vpa("invalid-vpa").is_valid is False
