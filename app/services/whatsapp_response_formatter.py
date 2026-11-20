"""Deterministic, language-consistent WhatsApp transaction responses."""

from decimal import Decimal

from app.services.confirmation import PostingResult


_COPY = {
    ("te", "Latin"): {
        "confirm_heading": "Transaction vivaralu",
        "party": "Customer",
        "paid": "Vachina payment",
        "credit": "Migatha baaki",
        "posted_credit": "Migatha baaki",
        "total": "Mottham",
        "candidate": "Candidate",
        "pending": "Inka ledger lo record avvaledu.",
        "review": "Munduga missing details correct chesi malli validate cheyyali.",
        "question": "Ee transaction record cheyyala?",
        "confirm": "✅ Confirm",
        "cancel": "❌ Cancel",
        "posted": "Transaction record ayyindi",
        "status": "Sthithi",
        "transaction": "Transaction",
        "inventory": "Migatha stock",
        "no_inventory": "Stock maaraledu",
        "cancelled": "Transaction cancel ayyindi. Emi record avvaledu.",
    },
    ("te", "Telugu"): {
        "confirm_heading": "లావాదేవీ వివరాలు",
        "party": "కస్టమర్",
        "paid": "అందిన చెల్లింపు",
        "credit": "మిగిలిన బాకీ",
        "posted_credit": "మిగిలిన బాకీ",
        "total": "మొత్తం",
        "candidate": "అభ్యర్థి",
        "pending": "ఇంకా లెడ్జర్‌లో నమోదు కాలేదు.",
        "review": "ముందుగా మిస్సింగ్ వివరాలను సరిచేసి మళ్లీ ధృవీకరించాలి.",
        "question": "ఈ లావాదేవీని నమోదు చేయాలా?",
        "confirm": "✅ నిర్ధారించు",
        "cancel": "❌ రద్దు",
        "posted": "లావాదేవీ నమోదైంది",
        "status": "స్థితి",
        "transaction": "లావాదేవీ",
        "inventory": "మిగిలిన స్టాక్",
        "no_inventory": "స్టాక్ మారలేదు",
        "cancelled": "లావాదేవీ రద్దయింది. ఏదీ నమోదు కాలేదు.",
    },
    ("hi", "Devanagari"): {
        "confirm_heading": "लेन-देन का विवरण",
        "party": "ग्राहक",
        "paid": "प्राप्त भुगतान",
        "credit": "बाकी उधार",
        "posted_credit": "बाकी उधार",
        "total": "कुल",
        "candidate": "उम्मीदवार",
        "pending": "अभी लेजर में दर्ज नहीं हुआ है।",
        "review": "पहले अधूरी जानकारी सही करके दोबारा जाँच करनी होगी।",
        "question": "क्या यह लेन-देन दर्ज करें?",
        "confirm": "✅ पुष्टि करें",
        "cancel": "❌ रद्द करें",
        "posted": "लेन-देन दर्ज हो गया",
        "status": "स्थिति",
        "transaction": "लेन-देन",
        "inventory": "बाकी स्टॉक",
        "no_inventory": "स्टॉक नहीं बदला",
        "cancelled": "लेन-देन रद्द हुआ। कुछ भी दर्ज नहीं हुआ।",
    },
    ("hi", "Latin"): {
        "confirm_heading": "Len-den ki jankari",
        "party": "Customer",
        "paid": "Mila payment",
        "credit": "Baaki udhaar",
        "posted_credit": "Baaki udhaar",
        "total": "Kul",
        "candidate": "Candidate",
        "pending": "Abhi ledger mein darj nahi hua hai.",
        "review": "Pehle adhuri jankari sahi karke dobara jaanch karni hogi.",
        "question": "Kya yeh len-den darj karein?",
        "confirm": "✅ Confirm",
        "cancel": "❌ Cancel",
        "posted": "Len-den darj ho gaya",
        "status": "Sthiti",
        "transaction": "Transaction",
        "inventory": "Baaki stock",
        "no_inventory": "Stock nahi badla",
        "cancelled": "Len-den cancel hua. Kuch bhi darj nahi hua.",
    },
    ("en", "Latin"): {
        "confirm_heading": "Transaction details",
        "party": "Customer",
        "paid": "Paid",
        "credit": "Credit",
        "posted_credit": "Credit outstanding",
        "total": "Total",
        "candidate": "Candidate",
        "pending": "Nothing posted yet.",
        "review": "Correct the missing details and revalidate before confirming.",
        "question": "Record this transaction?",
        "confirm": "✅ Confirm",
        "cancel": "❌ Cancel",
        "posted": "Transaction recorded",
        "status": "Status",
        "transaction": "Transaction",
        "inventory": "Inventory balance",
        "no_inventory": "No inventory movement",
        "cancelled": "Transaction cancelled. Nothing was posted.",
    },
}


def copy_for(language: str, script: str) -> dict[str, str]:
    return _COPY.get((language, script), _COPY[("en", "Latin")])


def hold_message(reasons: list[str], language: str, script: str) -> str:
    translations = {
        "Insufficient stock": {
            ("te", "Latin"): "Ee sale ki saripoye stock ledu.",
            ("te", "Telugu"): "ఈ అమ్మకానికి సరిపడా స్టాక్ లేదు.",
            ("hi", "Latin"): "Is sale ke liye stock kaafi nahi hai.",
            ("hi", "Devanagari"): "इस बिक्री के लिए पर्याप्त स्टॉक नहीं है।",
        },
    }
    return "; ".join(
        translations.get(reason, {}).get((language, script), reason) for reason in reasons
    )


def _amount(value) -> str:
    if value is None:
        return "—"
    number = Decimal(str(value))
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _posted_amount(value) -> str:
    return f"{Decimal(str(value or 0)):.2f}"


def _currency(code: str | None) -> str:
    return {"INR": "₹", "USD": "$"}.get(code or "", f"{code} " if code else "")


def candidate_summary(candidate, candidate_id: str, language: str, script: str,
                      *, review: bool = False) -> str:
    labels = copy_for(language, script)
    symbol = _currency(candidate.currency)
    lines = [f"*{labels['confirm_heading']}*", f"{labels['party']}: {candidate.buyer_name or '—'}"]
    for item in candidate.items:
        line = f"• {item.item_name}: {_amount(item.quantity)}"
        if item.unit_price is not None:
            line += f" × {symbol}{_amount(item.unit_price)}"
        if item.line_total is not None:
            line += f" = {symbol}{_amount(item.line_total)}"
        lines.append(line)
    for payment in candidate.payments:
        lines.append(f"{payment.method.upper()}: {symbol}{_amount(payment.amount)}")
    lines.extend([
        f"{labels['paid']}: {symbol}{_amount(candidate.amount_paid)}",
        f"{labels['credit']}: {symbol}{_amount(candidate.credit_amount)}",
        f"{labels['total']}: {symbol}{_amount(candidate.total_amount)}",
        f"{labels['candidate']}: {candidate_id}",
        labels["pending"],
    ])
    if review:
        lines.append(labels["review"])
    else:
        lines.append(labels["question"])
    return "\n".join(lines)


def posting_summary(result: PostingResult, language: str, script: str) -> str:
    labels = copy_for(language, script)
    balances = ", ".join(f"{sku}: {quantity}" for sku, quantity in result.inventory_balance.items())
    return "\n".join([
        f"✅ *{labels['posted']}*",
        f"{labels['status']}: {result.posting_status}",
        f"{labels['transaction']}: {result.transaction_id}",
        f"{labels['paid']}: {_posted_amount(result.amount_paid)}",
        f"{labels['posted_credit']}: {_posted_amount(result.credit_outstanding)}",
        f"{labels['inventory']}: {balances or labels['no_inventory']}",
    ])
