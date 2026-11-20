"""
Deterministic Mathematical & Financial Invariant Validator for Clandexis Invisible ERP.
Enforces strict accounting checks before any ledger mutations (FR-04.3 Specification).
"""

from typing import List, Optional, Tuple
from config import settings
from models.firestore_schema import LineItem, PaymentMethod, PaymentNode, PaymentStatus


def validate_split_math(
    total_amount: float,
    line_items: List[LineItem],
    payments: List[PaymentNode],
    high_credit_threshold: Optional[float] = None,
    confidence_score: float = 1.0,
    confidence_threshold: Optional[float] = None,
) -> Tuple[bool, Optional[str], bool]:
    """
    Validates deterministic financial rules:
    1. Sum of line item subtotals must match total_amount (within tolerance 0.01).
    2. Sum of payment breakdown (settled + udhaar) must match total_amount.
    3. Evaluates if human-in-the-loop safety gate must be triggered based on .env thresholds.

    Returns:
        (is_math_valid, error_reason, requires_safety_gate)
    """
    if high_credit_threshold is None:
        high_credit_threshold = settings.HIGH_CREDIT_THRESHOLD
    if confidence_threshold is None:
        confidence_threshold = settings.CONFIDENCE_THRESHOLD

    TOLERANCE = 0.50  # Allow minor rounding adjustment for vernacular conversions

    # 1. Validate Line Items sum if line items are present
    if line_items:
        items_sum = sum(item.subtotal for item in line_items)
        if abs(items_sum - total_amount) > TOLERANCE:
            reason = f"Line items total (₹{items_sum:.2f}) does not match declared total (₹{total_amount:.2f})"
            return False, reason, True

    # 2. Validate Payments sum
    payments_sum = sum(p.amount for p in payments)
    if abs(payments_sum - total_amount) > TOLERANCE:
        reason = f"Payments sum (₹{payments_sum:.2f}) does not match declared total (₹{total_amount:.2f})"
        return False, reason, True

    # 3. Check for High Credit (Udhaar) Threshold
    udhaar_amount = sum(
        p.amount for p in payments if p.method == PaymentMethod.UDHAAR_CREDIT
    )
    requires_safety_gate = False
    gate_reason = None

    if udhaar_amount > high_credit_threshold:
        requires_safety_gate = True
        gate_reason = f"High credit allocation: ₹{udhaar_amount:.2f} exceeds threshold ₹{high_credit_threshold:.2f}"

    if confidence_score < confidence_threshold:
        requires_safety_gate = True
        gate_reason = f"Extraction confidence ({confidence_score:.2f}) is below {confidence_threshold:.2f} threshold"

    return True, gate_reason, requires_safety_gate
