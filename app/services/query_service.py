"""Merchant-scoped, read-only WhatsApp queries over canonical persistence."""

from decimal import Decimal
import re

from app.services.confirmation import AuthenticatedMerchant
from app.services.persistence import ledger
from models.canonical import OutboundMessage


def _value(row, name, default=None):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _number(value):
    amount = Decimal(str(value or 0))
    return f"{amount:,.2f}" if amount != amount.to_integral() else f"{amount:,.0f}"


class QueryService:
    """Recognize supported non-mutating intents and answer from one merchant scope."""

    _ACTION = re.compile(
        r"\b(sold|sell|sale|becha|bechi|ammanu|ammnu|purchase|bought|"
        r"received|repaid|paid|add|added|produce|produced|manufactured|"
        r"banaya|banayi|record|damage|wastage|have|opening|intake)\b|అమ్మ|కొన్న|ఉన్నాయి|बेच|खरीद",
        re.I,
    )
    _REPORT = re.compile(r"\b(pdf|ledgers?|reports?|statement export)\b", re.I)
    _LOW_STOCK = re.compile(
        r"\b(low stock|reorder|running out|khatam|kam hai|thakkuva|takkuva)\b|"
        r"स्टॉक कम|खत्म|తక్కువ|అయిపో",
        re.I,
    )
    _STOCK = re.compile(
        r"\b(stock|inventory|maal)\b.*\b(kitna|entha|how much|current|show|dikhao|hai)\b|"
        r"\b(kitna|entha|how much|current|show|dikhao)\b.*\b(stock|inventory|maal)\b|"
        r"स्टॉक.*(कितना|दिखाओ)|స్టాక్.*(ఎంత|చూప)",
        re.I,
    )
    _PARTY_LIST = re.compile(
        r"\b(customer|customers|party|parties|grahak)\s*(list|dikhao|show|soochi|suchi)\b|"
        r"ग्राहक.*(सूची|दिखाओ)|కస్టమర్.*(జాబితా|చూప)",
        re.I,
    )
    _BALANCE = re.compile(
        r"\b(udhaar|credit|outstanding|balance|khata|baaki|baki)\b.*\b(kitna|entha|show|dikhao|hai)\b|"
        r"\b(kitna|entha|show|dikhao)\b.*\b(udhaar|credit|outstanding|balance|khata|baaki|baki)\b|"
        r"उधार.*(कितना|दिखाओ)|బాకీ.*(ఎంత|చూప)",
        re.I,
    )
    _SUMMARY = re.compile(
        r"\b(daily|today|aaj|eeroju)\b.*\b(summary|sales|bikri|collection|business)\b|"
        r"\b(summary|sales summary|business pulse)\b|"
        r"आज.*(बिक्री|सारांश)|ఈరోజు.*(అమ్మక|సారాంశ)",
        re.I,
    )

    def handle(
        self,
        merchant: AuthenticatedMerchant,
        text: str,
        recipient_phone: str,
        *,
        language: str = "en",
        script: str = "Latin",
    ) -> OutboundMessage | None:
        value = (text or "").strip()
        lowered = value.casefold()
        if not value:
            return None
        if lowered in {"hi", "hello", "hey", "namaste", "నమస్తే", "నమస్కారం", "नमस्ते"}:
            return self._out(
                recipient_phone, self._help(language, script, greeting=True)
            )
        if lowered in {"help", "menu", "madad", "సహాయం", "मदद"}:
            return self._out(
                recipient_phone, self._help(language, script, greeting=False)
            )
        if self._REPORT.search(value):
            return self._out(
                recipient_phone, self._unsupported_report(language, script)
            )
        if self._ACTION.search(value):
            return None
        if self._LOW_STOCK.search(value):
            return self._inventory(
                merchant.merchant_id, recipient_phone, language, script, low_only=True
            )
        if self._STOCK.search(value):
            return self._inventory(
                merchant.merchant_id, recipient_phone, language, script, low_only=False
            )
        if self._PARTY_LIST.search(value):
            return self._parties(
                merchant.merchant_id, recipient_phone, language, script
            )
        if self._BALANCE.search(value):
            return self._balance(
                merchant.merchant_id, value, recipient_phone, language, script
            )
        if self._SUMMARY.search(value):
            return self._summary(
                merchant.merchant_id, recipient_phone, language, script
            )
        return None

    @staticmethod
    def _out(phone, text):
        return OutboundMessage(recipient_phone=phone, text_content=text)

    def _inventory(self, merchant_id, phone, language, script, *, low_only):
        items = list(ledger.get_inventory(merchant_id))
        if low_only:
            items = [
                item for item in items if _value(item, "below_reorder_level", False)
            ]
        if not items:
            messages = {
                "te": (
                    "రీఆర్డర్ స్థాయికి దిగువన ఉన్న వస్తువులు లేవు."
                    if script == "Telugu"
                    else "Reorder level kanna thakkuva stock emi ledu."
                ),
                "hi": (
                    "रीऑर्डर स्तर से कम कोई वस्तु नहीं है।"
                    if script == "Devanagari"
                    else "Reorder level se kam koi item nahi hai."
                ),
                "en": "No items are below their reorder level.",
            }
            return self._out(phone, messages.get(language, messages["en"]))
        title = self._labels(language, script)["low_stock" if low_only else "stock"]
        lines = [
            f"• {_value(item, 'item_name')}: {_number(_value(item, 'current_stock'))} {_value(item, 'unit', '')}"
            for item in items
        ]
        return self._out(phone, title + "\n" + "\n".join(lines))

    def _parties(self, merchant_id, phone, language, script):
        parties = list(ledger.list_parties(merchant_id))
        title = self._labels(language, script)["customers"]
        if not parties:
            return self._out(phone, title + "\n—")
        lines = [
            f"• {_value(p, 'display_name')}: {_number(_value(p, 'current_udhaar_balance'))}"
            for p in parties
        ]
        return self._out(phone, title + "\n" + "\n".join(lines))

    def _balance(self, merchant_id, text, phone, language, script):
        parties = list(ledger.list_parties(merchant_id))
        lowered = text.casefold()
        matches = [
            p for p in parties if _value(p, "display_name", "").casefold() in lowered
        ]
        if not matches:
            significant = {
                token
                for token in re.findall(r"[a-zA-Z]+", lowered)
                if token
                not in {
                    "ka",
                    "ki",
                    "hai",
                    "kitna",
                    "entha",
                    "show",
                    "dikhao",
                    "udhaar",
                    "credit",
                    "outstanding",
                    "balance",
                    "khata",
                    "baaki",
                    "baki",
                }
            }
            matches = [
                p
                for p in parties
                if significant
                & set(
                    re.findall(r"[a-zA-Z]+", _value(p, "display_name", "").casefold())
                )
            ]
        if len(matches) != 1:
            prompts = {
                "te": (
                    "ఏ కస్టమర్ బాకీ కావాలి?"
                    if script == "Telugu"
                    else "Ye customer baaki kavali?"
                ),
                "hi": (
                    "किस ग्राहक का उधार देखना है?"
                    if script == "Devanagari"
                    else "Kis customer ka udhaar dekhna hai?"
                ),
                "en": "Which customer's outstanding balance do you want?",
            }
            return self._out(phone, prompts.get(language, prompts["en"]))
        party = matches[0]
        label = self._labels(language, script)["outstanding"]
        return self._out(
            phone,
            f"{_value(party, 'display_name')} — {label}: {_number(_value(party, 'current_udhaar_balance'))}",
        )

    def _summary(self, merchant_id, phone, language, script):
        row = ledger.get_daily_summary(merchant_id)
        labels = self._labels(language, script)
        return self._out(
            phone,
            "\n".join(
                [
                    labels["summary"],
                    f"{labels['sales']}: {_number(_value(row, 'sales_total'))}",
                    f"{labels['collections']}: {_number(_value(row, 'collections_total'))}",
                    f"UPI: {_number(_value(row, 'upi_received'))}",
                    f"{labels['outstanding']}: {_number(_value(row, 'total_receivables_outstanding'))}",
                ]
            ),
        )

    @staticmethod
    def _help(language, script, *, greeting):
        if language == "te" and script == "Telugu":
            start = "నమస్కారం!" if greeting else "నేను సహాయం చేయగలను:"
            return (
                start
                + "\n• స్టాక్ ఎంత?\n• సురేష్ బాకీ ఎంత?\n• ఈరోజు అమ్మకాల సారాంశం\nలావాదేవీలు నిర్ధారణ తర్వాత మాత్రమే పోస్ట్ అవుతాయి."
            )
        if language == "te":
            start = "Namaskaram!" if greeting else "Nenu veetilo sahayam chestanu:"
            return (
                start
                + "\n• Stock entha?\n• Suresh baaki entha?\n• Eeroju sales summary\nTransaction Confirm chesina tarvata matrame post avutundi."
            )
        if language == "hi" and script == "Devanagari":
            start = "नमस्ते!" if greeting else "मैं इनमें मदद कर सकता हूँ:"
            return (
                start
                + "\n• स्टॉक कितना है?\n• सुरेश का उधार कितना है?\n• आज की बिक्री का सारांश\nलेन-देन केवल पुष्टि के बाद पोस्ट होते हैं।"
            )
        if language == "hi":
            start = "Namaste!" if greeting else "Main inmein madad kar sakta hoon:"
            return (
                start
                + "\n• Stock kitna hai?\n• Suresh ka udhaar kitna hai?\n• Aaj ki sales summary\nTransaction Confirm ke baad hi post hota hai."
            )
        start = "Hello!" if greeting else "I can help with:"
        return (
            start
            + "\n• Current stock\n• Customer outstanding balance\n• Today's sales and collections\nTransactions post only after explicit confirmation."
        )

    @staticmethod
    def _unsupported_report(language, script):
        if language == "te" and script == "Telugu":
            return "PDF లేదా పూర్తి లెడ్జర్ రిపోర్ట్ కోసం ప్రమాణీకరించిన డాష్‌బోర్డ్ ఉపయోగించండి. ఏ లావాదేవీ పోస్ట్ కాలేదు."
        if language == "te":
            return "PDF leka full ledger report kosam authenticated dashboard vadandi. Transaction post avvaledu."
        if language == "hi" and script == "Devanagari":
            return "PDF या पूरी लेजर रिपोर्ट के लिए प्रमाणित डैशबोर्ड का उपयोग करें। कोई लेन-देन पोस्ट नहीं हुआ।"
        if language == "hi":
            return "PDF ya full ledger report ke liye authenticated dashboard use karein. Koi transaction post nahi hua."
        return "Use the authenticated dashboard for PDF or full ledger reports. No transaction was posted."

    @staticmethod
    def _labels(language, script):
        if language == "te" and script == "Telugu":
            return {
                "stock": "ప్రస్తుత స్టాక్",
                "low_stock": "తక్కువ స్టాక్",
                "customers": "కస్టమర్లు మరియు బాకీలు",
                "outstanding": "బాకీ",
                "summary": "ఈరోజు వ్యాపార సారాంశం",
                "sales": "అమ్మకాలు",
                "collections": "వసూళ్లు",
            }
        if language == "te":
            return {
                "stock": "Prastuta stock",
                "low_stock": "Thakkuva stock",
                "customers": "Customers mariyu baakilu",
                "outstanding": "Baaki",
                "summary": "Eeroju business summary",
                "sales": "Sales",
                "collections": "Collections",
            }
        if language == "hi" and script == "Devanagari":
            return {
                "stock": "वर्तमान स्टॉक",
                "low_stock": "कम स्टॉक",
                "customers": "ग्राहक और उधार",
                "outstanding": "बकाया",
                "summary": "आज का व्यवसाय सारांश",
                "sales": "बिक्री",
                "collections": "वसूली",
            }
        if language == "hi":
            return {
                "stock": "Vartaman stock",
                "low_stock": "Kam stock",
                "customers": "Customers aur udhaar",
                "outstanding": "Bakaya",
                "summary": "Aaj ka business summary",
                "sales": "Sales",
                "collections": "Collections",
            }
        return {
            "stock": "Current stock",
            "low_stock": "Low stock",
            "customers": "Customers and outstanding",
            "outstanding": "Outstanding",
            "summary": "Today's business summary",
            "sales": "Sales",
            "collections": "Collections",
        }
