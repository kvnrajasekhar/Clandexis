"""BusinessValidator must not turn float noise into false overpayment / mismatch."""
from app.schemas.transaction import LineItem, PaymentRecord, TransactionCandidate
from app.services.validator import BusinessValidator


def _sale(total, items_total, payments, amount_paid, credit=None):
    return TransactionCandidate(
        transaction_type="sale", buyer_name="Ramesh",
        items=[LineItem(item_name="x", quantity=3, unit_price=round(items_total / 3, 2), line_total=items_total)],
        total_amount=total, payments=[PaymentRecord(method=m, amount=a) for m, a in payments],
        amount_paid=amount_paid, credit_amount=credit)


def test_50_plus_49_99_is_not_overpayment():
    assert 50.00 + 49.99 != 99.99          # the float noise being guarded against
    r = BusinessValidator.validate_candidate(_sale(99.99, 99.99, [("cash", 50.00), ("upi", 49.99)], 50.00 + 49.99, 0.0))
    assert r.validation_status == "ACCEPTED", r.validation_warnings


def test_genuine_overpayment_still_rejected():
    r = BusinessValidator.validate_candidate(_sale(99.99, 99.99, [("cash", 50.00), ("upi", 50.00)], 100.00, 0.0))
    assert r.validation_status == "REJECTED"


def test_genuine_one_paisa_overpayment_rejected():
    r = BusinessValidator.validate_candidate(_sale(99.99, 99.99, [("cash", 50.00), ("upi", 50.00)], 50.00 + 50.00, 0.0))
    assert r.validation_status == "REJECTED"


def test_float_noise_in_total_and_credit():
    r = BusinessValidator.validate_candidate(
        _sale(0.1 + 0.2, 0.3, [("cash", 0.1)], 0.1, 0.2))      # 0.30000000000000004 total
    assert r.validation_status == "ACCEPTED", r.validation_warnings
