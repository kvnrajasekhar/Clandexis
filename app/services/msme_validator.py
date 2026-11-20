"""
MSME Business Validation Service for Clandexis Invisible ERP.
Handles deterministic verification of:
- Indian GSTIN (15-character format, valid State Code, internal PAN, and Mod-36 Luhn Checksum)
- Udyam Registration Number (URN) (e.g., UDYAM-TS-02-0012345)
- Indian PAN (Permanent Account Number, 10 characters)
- 6-digit Indian Postal PIN Code with State & Vernacular Language inference
- UPI VPA (Virtual Payment Address)
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Official 2-digit Indian State Codes for GST
GST_STATE_CODES: Dict[str, str] = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (Old)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}

# 2-letter state abbreviations for Udyam
UDYAM_STATE_CODES = {
    "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ", "HP",
    "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP", "MZ",
    "NL", "OD", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK", "UP", "WB"
}

# Mapping 2-digit postal pincode prefixes to State and recommended language
PINCODE_STATE_MAP: Dict[str, Tuple[str, str]] = {
    "11": ("Delhi", "hi"),
    "12": ("Haryana", "hi"),
    "13": ("Haryana", "hi"),
    "14": ("Punjab", "pa"),
    "15": ("Punjab", "pa"),
    "16": ("Punjab / Chandigarh", "pa"),
    "17": ("Himachal Pradesh", "hi"),
    "18": ("Jammu & Kashmir", "ur"),
    "19": ("Jammu & Kashmir", "ur"),
    "20": ("Uttar Pradesh", "hi"),
    "21": ("Uttar Pradesh", "hi"),
    "22": ("Uttar Pradesh", "hi"),
    "23": ("Uttar Pradesh", "hi"),
    "24": ("Uttar Pradesh / Uttarakhand", "hi"),
    "25": ("Uttar Pradesh", "hi"),
    "26": ("Uttarakhand", "hi"),
    "27": ("Uttar Pradesh", "hi"),
    "28": ("Uttar Pradesh", "hi"),
    "30": ("Rajasthan", "hi"),
    "31": ("Rajasthan", "hi"),
    "32": ("Rajasthan", "hi"),
    "33": ("Rajasthan", "hi"),
    "34": ("Rajasthan", "hi"),
    "36": ("Gujarat", "gu"),
    "37": ("Gujarat", "gu"),
    "38": ("Gujarat", "gu"),
    "39": ("Gujarat", "gu"),
    "40": ("Maharashtra / Goa", "mr"),
    "41": ("Maharashtra", "mr"),
    "42": ("Maharashtra", "mr"),
    "43": ("Maharashtra", "mr"),
    "44": ("Maharashtra", "mr"),
    "45": ("Madhya Pradesh", "hi"),
    "46": ("Madhya Pradesh", "hi"),
    "47": ("Madhya Pradesh", "hi"),
    "48": ("Madhya Pradesh", "hi"),
    "49": ("Chhattisgarh", "hi"),
    "50": ("Telangana", "te"),
    "51": ("Andhra Pradesh", "te"),
    "52": ("Andhra Pradesh", "te"),
    "53": ("Andhra Pradesh", "te"),
    "56": ("Karnataka", "kn"),
    "57": ("Karnataka", "kn"),
    "58": ("Karnataka", "kn"),
    "59": ("Karnataka", "kn"),
    "60": ("Tamil Nadu", "ta"),
    "61": ("Tamil Nadu", "ta"),
    "62": ("Tamil Nadu", "ta"),
    "63": ("Tamil Nadu", "ta"),
    "64": ("Tamil Nadu", "ta"),
    "67": ("Kerala", "ml"),
    "68": ("Kerala", "ml"),
    "69": ("Kerala", "ml"),
    "70": ("West Bengal", "bn"),
    "71": ("West Bengal", "bn"),
    "72": ("West Bengal", "bn"),
    "73": ("West Bengal", "bn"),
    "74": ("West Bengal", "bn"),
    "75": ("Odisha", "or"),
    "76": ("Odisha", "or"),
    "77": ("Odisha", "or"),
    "80": ("Bihar", "hi"),
    "81": ("Bihar / Jharkhand", "hi"),
    "82": ("Jharkhand", "hi"),
    "83": ("Jharkhand", "hi"),
    "84": ("Bihar", "hi"),
    "85": ("Bihar", "hi"),
}


@dataclass
class ValidationResult:
    is_valid: bool
    value: str
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def compute_gstin_checksum(gstin_14: str) -> str:
    """
    Computes the official 15th check character of an Indian GSTIN using
    the Luhn Mod-36 algorithm as specified by the GSTN.
    """
    total = 0
    for i, ch in enumerate(gstin_14):
        code_point = GST_CHARS.index(ch)
        factor = 1 if (i % 2 == 0) else 2
        prod = code_point * factor
        total += (prod // 36) + (prod % 36)
    rem = total % 36
    check_idx = (36 - rem) % 36
    return GST_CHARS[check_idx]


def validate_gstin(gstin: Optional[str], strict_checksum: bool = True) -> ValidationResult:
    """
    Validates Indian GSTIN (Goods and Services Tax Identification Number).
    Format: 2 state digits + 10-char PAN + 1 entity char + 'Z' + 1 checksum char.
    """
    if not gstin or not isinstance(gstin, str):
        return ValidationResult(is_valid=False, value="", error="GSTIN is empty or missing")

    cleaned = re.sub(r"[\s-]", "", gstin).upper()
    if len(cleaned) != 15:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error=f"GSTIN must be exactly 15 characters, got {len(cleaned)}"
        )

    pattern = r"^([0-9]{2})([A-Z]{5}[0-9]{4}[A-Z]{1})([1-9A-Z]{1})(Z)([0-9A-Z]{1})$"
    match = re.match(pattern, cleaned)
    if not match:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error="Invalid GSTIN format (expected 2-digit state, 10-digit PAN, entity code, Z, check digit)"
        )

    state_code, pan, entity_code, z_char, check_char = match.groups()

    if state_code not in GST_STATE_CODES:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error=f"Invalid state code '{state_code}' in GSTIN"
        )

    pan_res = validate_pan(pan)
    if not pan_res.is_valid:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error=f"Embedded PAN '{pan}' in GSTIN is invalid: {pan_res.error}"
        )

    expected_check = compute_gstin_checksum(cleaned[:14])
    if strict_checksum and check_char != expected_check:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error=f"Invalid GSTIN checksum digit (got '{check_char}', expected '{expected_check}')",
            metadata={
                "state_code": state_code,
                "state_name": GST_STATE_CODES[state_code],
                "pan": pan,
                "expected_checksum": expected_check,
            }
        )

    return ValidationResult(
        is_valid=True,
        value=cleaned,
        metadata={
            "state_code": state_code,
            "state_name": GST_STATE_CODES[state_code],
            "pan": pan,
            "entity_code": entity_code,
            "business_type": pan_res.metadata.get("entity_type", "Business"),
        }
    )


def validate_udyam(urn: Optional[str]) -> ValidationResult:
    """
    Validates Udyam Registration Number (URN) for Indian MSMEs.
    Format: UDYAM-XX-00-0000000 (e.g. UDYAM-TS-02-0012345).
    """
    if not urn or not isinstance(urn, str):
        return ValidationResult(is_valid=False, value="", error="Udyam number is empty or missing")

    cleaned = urn.strip().upper()
    pattern = r"^UDYAM-([A-Z]{2})-([0-9]{2})-([0-9]{7})$"
    match = re.match(pattern, cleaned)
    if not match:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error="Invalid Udyam format. Must be UDYAM-XX-00-0000000 (e.g. UDYAM-TS-04-0019284)"
        )

    state_code, district_code, reg_num = match.groups()
    if state_code not in UDYAM_STATE_CODES:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error=f"Unknown state code '{state_code}' in Udyam number"
        )

    return ValidationResult(
        is_valid=True,
        value=cleaned,
        metadata={
            "state_code": state_code,
            "district_code": district_code,
            "registration_number": reg_num,
        }
    )


def validate_pan(pan: Optional[str]) -> ValidationResult:
    """
    Validates Indian Permanent Account Number (PAN).
    Format: 5 letters + 4 digits + 1 letter (e.g., AAACB1234M).
    4th character denotes entity type:
    P = Individual / Proprietor, F = Partnership Firm, C = Company, H = HUF.
    """
    if not pan or not isinstance(pan, str):
        return ValidationResult(is_valid=False, value="", error="PAN is empty or missing")

    cleaned = re.sub(r"[\s-]", "", pan).upper()
    pattern = r"^[A-Z]{3}([PCHFATBLJG])[A-Z]{1}[0-9]{4}[A-Z]{1}$"
    match = re.match(pattern, cleaned)
    if not match:
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error="Invalid PAN format (expected 5 letters, 4 digits, 1 letter)"
        )

    entity_code = match.group(1)
    entity_names = {
        "P": "Individual / Proprietorship",
        "F": "Partnership Firm / LLP",
        "C": "Company / Corporation",
        "H": "Hindu Undivided Family (HUF)",
        "A": "Association of Persons (AOP)",
        "T": "Trust",
        "B": "Body of Individuals (BOI)",
        "L": "Local Authority",
        "J": "Artificial Juridical Person",
        "G": "Government Agency",
    }

    return ValidationResult(
        is_valid=True,
        value=cleaned,
        metadata={
            "entity_code": entity_code,
            "entity_type": entity_names.get(entity_code, "Other Entity"),
        }
    )


def validate_pincode(pincode: Optional[str]) -> ValidationResult:
    """
    Validates Indian 6-digit Postal Index Number (PIN Code).
    Infers state and recommended vernacular language.
    """
    if not pincode or not isinstance(pincode, str):
        return ValidationResult(is_valid=False, value="", error="PIN Code is missing")

    cleaned = re.sub(r"\D", "", pincode)
    if len(cleaned) != 6 or cleaned[0] == "0":
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error="Invalid PIN Code (must be 6 digits, cannot start with 0)"
        )

    prefix = cleaned[:2]
    state_info = PINCODE_STATE_MAP.get(prefix)
    state_name = state_info[0] if state_info else "India"
    suggested_lang = state_info[1] if state_info else "hi"

    return ValidationResult(
        is_valid=True,
        value=cleaned,
        metadata={
            "state_name": state_name,
            "suggested_lang": suggested_lang,
        }
    )


def validate_upi_vpa(vpa: Optional[str]) -> ValidationResult:
    """
    Validates UPI Virtual Payment Address (e.g., srilakshmi@okicici, merchant@upi).
    """
    if not vpa or not isinstance(vpa, str):
        return ValidationResult(is_valid=False, value="", error="UPI ID is missing")

    cleaned = vpa.strip().lower()
    pattern = r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$"
    if not re.match(pattern, cleaned):
        return ValidationResult(
            is_valid=False,
            value=cleaned,
            error="Invalid UPI format (expected username@bank or phone@upi)"
        )

    return ValidationResult(is_valid=True, value=cleaned)
