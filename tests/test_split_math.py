"""
Unit tests for deterministic split-payment and inventory math invariants.
"""

import pytest
from models.firestore_schema import LineItem, PaymentMethod, PaymentNode, PaymentStatus
from agent_orchestrator.validate_split_math import validate_split_math


def test_valid_split_payment_with_high_udhaar():
    # Ramesh 50 shirts @ ₹200 = ₹10,000.
    # ₹2,000 UPI + ₹8,000 Udhaar.
    # Udhaar > ₹5,000 -> Safety Gate Triggered!
    line_items = [
        LineItem(
            sku_id="shirt-cotton-l",
            item_name="Cotton Shirt",
            quantity=50.0,
            unit="pieces",
            unit_price=200.0,
            discount=0.0,
            tax_rate=0.0,
            subtotal=10000.0,
        )
    ]

    payments = [
        PaymentNode(method=PaymentMethod.UPI, amount=2000.0, status=PaymentStatus.SETTLED),
        PaymentNode(method=PaymentMethod.UDHAAR_CREDIT, amount=8000.0, status=PaymentStatus.PENDING_COLLECTION),
    ]

    is_valid, reason, requires_gate = validate_split_math(
        total_amount=10000.0,
        line_items=line_items,
        payments=payments,
        high_credit_threshold=5000.0,
        confidence_score=0.92,
    )

    assert is_valid is True
    assert requires_gate is True
    assert "High credit allocation: ₹8000.00" in reason


def test_math_mismatch_detected():
    # declared total ₹10,000, but line items sum = ₹9,000
    line_items = [
        LineItem(
            item_name="Cotton Shirt",
            quantity=45.0,
            unit="pieces",
            unit_price=200.0,
            subtotal=9000.0,
        )
    ]
    payments = [
        PaymentNode(method=PaymentMethod.CASH, amount=10000.0, status=PaymentStatus.SETTLED)
    ]

    is_valid, reason, requires_gate = validate_split_math(
        total_amount=10000.0,
        line_items=line_items,
        payments=payments,
    )

    assert is_valid is False
    assert requires_gate is True
    assert "Line items total (₹9000.00) does not match" in reason


def test_low_confidence_triggers_safety_gate():
    payments = [
        PaymentNode(method=PaymentMethod.CASH, amount=500.0, status=PaymentStatus.SETTLED)
    ]

    is_valid, reason, requires_gate = validate_split_math(
        total_amount=500.0,
        line_items=[],
        payments=payments,
        confidence_score=0.78,  # Below 0.85
    )

    assert is_valid is True
    assert requires_gate is True
    assert "confidence (0.78) is below 0.85 threshold" in reason
