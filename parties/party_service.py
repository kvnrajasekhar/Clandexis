"""
Party & Customer Management Service for Clandexis Invisible ERP.
Handles multi-tenant customer onboarding, multilingual fuzzy party resolution,
Khata credit limits, opening balance setup, and read-only customer statement queries.
"""

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from models.firestore_schema import Party

logger = logging.getLogger("party-service")

# Vernacular honorifics and common Indian business suffixes to normalize
HONORIFICS = {
    "ji", "bhai", "bhaiya", "dada", "anna", "garu", "seth", "sahab",
    "saheb", "traders", "textiles", "stores", "store", "garments",
    "wholesale", "retail", "general store", "enterprise", "enterprises",
    "dokan", "kirana", "cloth", "cloth store"
}


class PartyService:
    """
    Core service managing counterparties (Customers, Suppliers) and their Khata accounts.
    Strictly tenant-scoped by merchant_id.
    """

    def __init__(
        self,
        parties_store: Dict[str, Dict[str, Party]],
        merchants_store: Optional[Dict[str, Any]] = None,
        transactions_store: Optional[Dict[str, Any]] = None,
    ):
        self.parties = parties_store
        self.merchants = merchants_store if merchants_store is not None else {}
        self.transactions = transactions_store if transactions_store is not None else {}

    def _ensure_tenant(self, merchant_id: str):
        if merchant_id not in self.parties:
            self.parties[merchant_id] = {}

    def _slugify(self, text: str) -> str:
        """Converts human name to deterministic unique slug: 'Gupta Stores' -> 'gupta-stores'."""
        cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
        slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
        return slug or "party"

    def _generate_aliases(self, display_name: str, custom_aliases: Optional[List[str]] = None) -> List[str]:
        """Generates comprehensive phonetic and colloquial aliases for Indian business names."""
        aliases = set()
        clean = display_name.strip().lower()
        aliases.add(clean)

        tokens = clean.split()
        if tokens:
            aliases.add(tokens[0])  # First name (e.g. 'ramesh')

        # Add honorific variations
        base_name = " ".join([t for t in tokens if t not in HONORIFICS])
        if base_name and base_name != clean:
            aliases.add(base_name)
            aliases.add(f"{base_name} ji")
            aliases.add(f"{base_name} bhai")

        if custom_aliases:
            for a in custom_aliases:
                if a and a.strip():
                    aliases.add(a.strip().lower())

        return sorted(list(aliases))

    def resolve_party(
        self,
        merchant_id: str,
        query_text: str,
    ) -> Tuple[Optional[Party], float]:
        """
        Multilingual fuzzy matcher for customer names in conversational text.
        Returns (matched_party, confidence_score [0.0 - 1.0]).
        """
        self._ensure_tenant(merchant_id)
        tenant_parties = self.parties.get(merchant_id, {})
        if not tenant_parties or not query_text:
            return None, 0.0

        q = query_text.lower().strip()
        best_match: Optional[Party] = None
        best_score = 0.0

        for party in tenant_parties.values():
            party_name_clean = party.display_name.lower()
            slug_clean = party.party_id.lower().replace("-", " ")

            # Exact match check
            if party_name_clean in q or q in party_name_clean:
                score = 0.95
                if score > best_score:
                    best_score = score
                    best_match = party

            # Check aliases
            for alias in party.aliases:
                a_clean = alias.lower()
                if a_clean in q:
                    score = 0.92
                    if score > best_score:
                        best_score = score
                        best_match = party
                else:
                    # Token-level similarity
                    sim = SequenceMatcher(None, a_clean, q).ratio()
                    if sim > 0.82 and sim > best_score:
                        best_score = sim
                        best_match = party

            # Slug matching
            if slug_clean in q:
                score = 0.90
                if score > best_score:
                    best_score = score
                    best_match = party

        return best_match, best_score

    def create_or_update_party(
        self,
        merchant_id: str,
        display_name: str,
        phone: Optional[str] = None,
        party_type: str = "CUSTOMER",
        gstin: Optional[str] = None,
        address: Optional[str] = None,
        credit_limit: float = 50000.0,
        opening_udhaar_balance: float = 0.0,
        payment_terms_days: int = 15,
        custom_aliases: Optional[List[str]] = None,
    ) -> Tuple[Party, str]:
        """
        Creates or updates a customer/counterparty master record in the merchant's tenant.
        Returns the Party and formatted WhatsApp confirmation.
        """
        self._ensure_tenant(merchant_id)
        slug = self._slugify(display_name)
        aliases = self._generate_aliases(display_name, custom_aliases)

        existing = self.parties[merchant_id].get(slug)
        if existing:
            existing.display_name = display_name.strip()
            if phone:
                existing.phone = phone.strip()
            existing.party_type = party_type
            if gstin:
                existing.gstin = gstin.strip()
            if address:
                existing.address = address.strip()
            existing.credit_limit = credit_limit
            if opening_udhaar_balance > 0:
                existing.current_udhaar_balance += opening_udhaar_balance
            existing.payment_terms_days = payment_terms_days
            existing.aliases = sorted(list(set(existing.aliases + aliases)))
            action_desc = "गराहक का विवरण अपडेट हुआ (Customer Updated)"
            party = existing
        else:
            party = Party(
                party_id=slug,
                display_name=display_name.strip(),
                aliases=aliases,
                phone=phone.strip() if phone else None,
                party_type=party_type,
                gstin=gstin.strip() if gstin else None,
                address=address.strip() if address else None,
                current_udhaar_balance=opening_udhaar_balance,
                credit_limit=credit_limit,
                payment_terms_days=payment_terms_days,
                last_txn_at=datetime.now(timezone.utc) if opening_udhaar_balance > 0 else None,
                created_at=datetime.now(timezone.utc),
            )
            self.parties[merchant_id][slug] = party
            action_desc = "नया ग्राहक खाता खोला गया (Customer Onboarded)"

        formatted_msg = (
            f"👤 *{action_desc}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ *Customer Name:* {party.display_name}\n"
            f"🔑 *Party ID:* `{party.party_id}`\n"
            f"📞 *Phone:* {party.phone or 'Not provided'}\n"
            f"💼 *Category:* {party.party_type}\n"
            f"🧾 *GSTIN:* {party.gstin or 'Unregistered / B2C'}\n"
            f"📍 *Address:* {party.address or 'N/A'}\n"
            f"💳 *Credit Limit (उधार सीमा):* ₹{party.credit_limit:,.2f}\n"
            f"💰 *Current Udhaar Balance:* ₹{party.current_udhaar_balance:,.2f}\n"
            f"⏳ *Payment Terms:* {party.payment_terms_days} days\n"
            f"🗣️ *Aliases:* {', '.join(party.aliases[:4])}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Khata account taiyar hai! Aap inke naam par sales ya udhaar darj kar sakte hain."
        )

        return party, formatted_msg

    def get_party_statement(
        self,
        merchant_id: str,
        party_query: str,
    ) -> Dict[str, Any]:
        """
        Pure read-only query returning counterparty statement & Khata health.
        Guarantees zero mutations.
        """
        self._ensure_tenant(merchant_id)
        party, conf = self.resolve_party(merchant_id, party_query)
        if not party:
            return {
                "found": False,
                "party": None,
                "formatted_text": f"⚠️ *Customer Not Found*\n`{party_query}` naam ka koi grahak nahi mila. Naya customer add karne ke liye likhein: 'Add customer {party_query}'.",
            }

        available_credit = max(0.0, party.credit_limit - party.current_udhaar_balance)
        utilization_pct = (party.current_udhaar_balance / party.credit_limit * 100) if party.credit_limit > 0 else 0.0

        risk_indicator = "🟢 Normal"
        if utilization_pct >= 90:
            risk_indicator = "🔴 High Risk (Limit Nearly Exhausted)"
        elif utilization_pct >= 70:
            risk_indicator = "🟡 Moderate Credit"

        last_txn_str = party.last_txn_at.strftime("%d %b %Y, %I:%M %p UTC") if party.last_txn_at else "No transactions yet"

        formatted = (
            f"📒 *Customer Khata Statement / Udhaar Ledger (उधार खाता)*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Party:* {party.display_name} (`{party.party_id}`)\n"
            f"📞 *Phone:* {party.phone or 'N/A'}\n"
            f"🧾 *GSTIN:* {party.gstin or 'N/A'}\n"
            f"💰 *Current Udhaar (उधार):* ₹{party.current_udhaar_balance:,.2f}\n"
            f"💳 *Credit Limit:* ₹{party.credit_limit:,.2f}\n"
            f"📉 *Available Headroom:* ₹{available_credit:,.2f}\n"
            f"📊 *Credit Utilization:* {utilization_pct:.1f}% ({risk_indicator})\n"
            f"⏳ *Agreed Payment Terms:* {party.payment_terms_days} days\n"
            f"🕒 *Last Activity:* {last_txn_str}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        return {
            "found": True,
            "party": party,
            "confidence": conf,
            "available_credit": available_credit,
            "utilization_pct": utilization_pct,
            "formatted_text": formatted,
        }

    def list_parties(
        self,
        merchant_id: str,
        party_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pure read-only query listing all counterparties and total outstanding receivables.
        Guarantees zero mutations.
        """
        self._ensure_tenant(merchant_id)
        parties = self.parties.get(merchant_id, {})
        
        filtered = [
            p for p in parties.values()
            if party_type is None or p.party_type == party_type
        ]

        total_udhaar = sum(p.current_udhaar_balance for p in filtered)

        if not filtered:
            msg = "📒 *Khata Summary*\nAapke khate mein abhi koi registered grahak nahi hain. Naya customer jodne ke liye likhein: 'Add customer [Name] phone [Number]'."
        else:
            lines = [
                f"📒 *All Registered Customers ({len(filtered)} Parties)*\n"
                f"━━━━━━━━━━━━━━━━━━━"
            ]
            for p in sorted(filtered, key=lambda x: x.current_udhaar_balance, reverse=True):
                phone_str = f" ({p.phone})" if p.phone else ""
                lines.append(
                    f"• *{p.display_name}*{phone_str}: ₹{p.current_udhaar_balance:,.2f} / Limit ₹{p.credit_limit:,.2f}"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━")
            lines.append(f"💰 *Total Outstanding Receivables:* ₹{total_udhaar:,.2f}")
            msg = "\n".join(lines)

        return {
            "total_parties": len(filtered),
            "total_receivables": total_udhaar,
            "parties": filtered,
            "formatted_text": msg,
        }
