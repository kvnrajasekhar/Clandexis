import re
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
from app.schemas.transaction import TransactionCandidate, ExtractionResponse, TransactionType

_CENT = Decimal("0.01")


def _m(value) -> Decimal:
    """Money -> exact 2-dp Decimal. Float noise from upstream sums (50.0 + 49.99 =
    99.99000000000001) is removed here, before any comparison, with ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


class BusinessValidator:
    """
    Stage 4: Business Validation & Financial Reconciliation
    - Strict decimal arithmetic for all monetary relationships.
    - Distinguishes missing payments from assumed credit.
    - Reconciles merchant role (buyer vs supplier -> purchase vs sale).
    - Detects overpayments (total < paid) -> REJECTED.
    - Detects line item unit * qty != line_total or != total_amount -> REJECTED or NEEDS_REVIEW.
    - Sets validation_status: ACCEPTED, REJECTED, or NEEDS_REVIEW.
    """

    @staticmethod
    def validate_candidate(tx: TransactionCandidate, current_merchant_id_or_name: Optional[str] = None) -> TransactionCandidate:
        warnings: List[str] = []
        requires_review = False
        is_rejected = False

        # --- Rule 1: Clean payments array (Move credit out if erroneously present) ---
        valid_payments = []
        for p in tx.payments:
            if "credit" in p.method.lower() or "udhaar" in p.method.lower():
                warnings.append(f"Payment entry '{p.method}' of {p.amount} moved from payments to credit_amount.")
                if tx.credit_amount is None:
                    tx.credit_amount = p.amount
            else:
                valid_payments.append(p)
        tx.payments = valid_payments

        # --- Rule 2: Derive unit_price_source provenance & validate line items ---
        for item in tx.items:
            if item.quantity is not None and item.line_total is not None and item.quantity > 0:
                calc_unit = _m(item.line_total) / Decimal(str(item.quantity))
                if item.unit_price is None:
                    item.unit_price = float(calc_unit)
                    item.unit_price_source = "derived"
                else:
                    stated_unit = Decimal(str(item.unit_price))
                    if abs(stated_unit - calc_unit) > Decimal("0.01"):
                        warnings.append(
                            f"Line item '{item.item_name}' arithmetic mismatch: {item.quantity} @ {stated_unit} = {Decimal(str(item.quantity)) * stated_unit}, but line_total is {item.line_total}."
                        )
                        is_rejected = True

        # --- Rule 3: Reconcile Line Items Sum vs Subtotal / Total ---
        if len(tx.items) > 0:
            items_total = sum(
                (_m(i.line_total) for i in tx.items if i.line_total is not None),
                Decimal("0")
            )
            # If no line_total, use qty * unit_price
            if items_total == Decimal("0"):
                items_total = sum(
                    (Decimal(str(i.quantity)) * Decimal(str(i.unit_price))
                     for i in tx.items if i.quantity is not None and i.unit_price is not None),
                    Decimal("0")
                )

            if items_total > Decimal("0") and tx.total_amount is not None:
                total_dec = _m(tx.total_amount)
                subtotal_dec = _m(tx.subtotal) if tx.subtotal is not None else None
                # If subtotal missing, compare items_total to total_amount (if no tax/discount)
                if subtotal_dec is None and (tx.tax_amount is None or tx.tax_amount == 0) and (tx.discount_amount is None or tx.discount_amount == 0):
                    if abs(items_total - total_dec) > Decimal("0.01"):
                        warnings.append(
                            f"Items sum ({items_total}) does not match declared total_amount ({total_dec})."
                        )
                        is_rejected = True
                elif subtotal_dec is not None and abs(items_total - subtotal_dec) > Decimal("0.01"):
                    warnings.append(
                        f"Items sum ({items_total}) does not match declared subtotal ({subtotal_dec})."
                    )
                    is_rejected = True

        # --- Rule 4: Subtotal + Tax - Discount against Total ---
        if tx.total_amount is not None and tx.subtotal is not None:
            sub_dec = _m(tx.subtotal)
            tax_dec = _m(tx.tax_amount) if tx.tax_amount is not None else Decimal("0")
            disc_dec = _m(tx.discount_amount) if tx.discount_amount is not None else Decimal("0")
            expected_tot = sub_dec + tax_dec - disc_dec
            actual_tot = _m(tx.total_amount)
            if abs(expected_tot - actual_tot) > Decimal("0.01"):
                warnings.append(
                    f"Subtotal ({sub_dec}) + Tax ({tax_dec}) - Discount ({disc_dec}) = {expected_tot}, but total_amount is {actual_tot}."
                )
                is_rejected = True

        # --- Rule 5: Payments & Overpayment Check ---
        paid_from_payments = sum((_m(p.amount) for p in tx.payments), Decimal("0"))
        
        # Check cash_amount + online_amount match payments
        if tx.cash_amount is not None or tx.online_amount is not None:
            calc_split = (_m(tx.cash_amount or 0) + _m(tx.online_amount or 0))
            if abs(calc_split - paid_from_payments) > Decimal("0.01") and len(tx.payments) > 0:
                warnings.append(f"Split payments sum ({calc_split}) differs from payments list sum ({paid_from_payments}).")
                requires_review = True

        if tx.amount_paid is not None:
            if abs(_m(tx.amount_paid) - paid_from_payments) > Decimal("0.01") and len(tx.payments) > 0:
                warnings.append(f"amount_paid ({tx.amount_paid}) differs from sum of payments ({paid_from_payments}).")
                requires_review = True
        elif len(tx.payments) > 0:
            tx.amount_paid = float(paid_from_payments)

        # Check for OVERPAYMENT: total_amount < amount_paid
        if tx.total_amount is not None and tx.amount_paid is not None:
            tot_dec = _m(tx.total_amount)
            paid_dec = _m(tx.amount_paid)
            if paid_dec > tot_dec:
                warnings.append(
                    f"Overpayment / Inconsistent payment total: Paid ({paid_dec}) exceeds Total Amount ({tot_dec})."
                )
                is_rejected = True

        # --- Rule 6: Missing payment info vs Assumed Credit Check ---
        # If sale/purchase has total_amount but NO payments mentioned AND no explicit credit stated:
        if tx.transaction_type in [TransactionType.SALE, TransactionType.PURCHASE]:
            if tx.total_amount is not None and len(tx.payments) == 0 and (tx.amount_paid is None or tx.amount_paid == 0):
                if tx.credit_amount is None or (tx.credit_amount == tx.total_amount and not tx.payment_terms):
                    # Check if the text actually mentioned credit/udhaar
                    transcript = (tx.raw_transcript or "").lower()
                    if not any(w in transcript for w in ["credit", "udhar", "udhaar", "baki", "baaki", "appu"]):
                        tx.needs_clarification = True
                        if not tx.clarification_question:
                            tx.clarification_question = "Was this paid, partially paid, or sold on credit?"
                        tx.credit_amount = None
                        tx.amount_paid = None
                        warnings.append("Payment status and method unmentioned. Requires merchant clarification.")
                        requires_review = True

        # --- Rule 7: Reconcile Merchant Role (Buyer vs Supplier -> Purchase vs Sale) ---
        if current_merchant_id_or_name:
            m_lower = current_merchant_id_or_name.lower()
            buyer_lower = (tx.buyer_name or "").lower()
            supp_lower = (tx.supplier_name or "").lower()

            if m_lower in buyer_lower or buyer_lower in m_lower:
                # Logged in merchant is the BUYER, so from their perspective it's a PURCHASE
                if tx.transaction_type == TransactionType.SALE:
                    warnings.append(f"Merchant '{current_merchant_id_or_name}' identified as Buyer. Adjusted transaction_type from 'sale' to 'purchase'.")
                    tx.transaction_type = TransactionType.PURCHASE
                    requires_review = True
            elif m_lower in supp_lower or supp_lower in m_lower:
                # Logged in merchant is the SUPPLIER, so it is a SALE
                if tx.transaction_type == TransactionType.PURCHASE:
                    warnings.append(f"Merchant '{current_merchant_id_or_name}' identified as Supplier. Adjusted transaction_type from 'purchase' to 'sale'.")
                    tx.transaction_type = TransactionType.SALE

        # --- Rule 8: Payment terms discrepancy check (e.g. '50% credit') ---
        if tx.payment_terms and tx.total_amount is not None:
            terms_lower = tx.payment_terms.lower()
            match = re.search(r"(\d+(\.\d+)?)%\s*credit", terms_lower)
            if match:
                expected_credit_pct = Decimal(match.group(1)) / Decimal("100")
                expected_credit = _m(tx.total_amount) * expected_credit_pct
                actual_credit = _m(tx.credit_amount) if tx.credit_amount is not None else Decimal("0")
                if abs(actual_credit - expected_credit) > Decimal("0.01"):
                    warnings.append(
                        f"Declared '{tx.payment_terms}' implies credit of {expected_credit:.2f}, but recorded credit_amount is {actual_credit:.2f}."
                    )
                    requires_review = True

        # --- Rule 9: Customer Name missing in Sale ---
        if tx.transaction_type == TransactionType.SALE and not tx.buyer_name and tx.needs_clarification:
            requires_review = True

        if tx.needs_clarification:
            requires_review = True

        tx.validation_warnings = warnings
        tx.requires_review = requires_review or is_rejected

        # Assign validation status
        if is_rejected:
            tx.validation_status = "REJECTED"
        elif tx.requires_review or tx.needs_clarification:
            tx.validation_status = "NEEDS_REVIEW"
        else:
            tx.validation_status = "ACCEPTED"

        return tx

    @classmethod
    def validate_response(cls, extraction: ExtractionResponse, current_merchant_id_or_name: Optional[str] = None) -> ExtractionResponse:
        validated_txs = [cls.validate_candidate(tx, current_merchant_id_or_name) for tx in extraction.transactions]
        return ExtractionResponse(transactions=validated_txs)
