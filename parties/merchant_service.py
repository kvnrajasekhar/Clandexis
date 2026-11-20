"""
MSME Merchant Service for Clandexis Invisible ERP.
Handles merchant profile setup, business details configuration (GSTIN, Udyam, UPI VPA),
language preferences, credit threshold settings, and profile analytics queries via WhatsApp.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from models.firestore_schema import Merchant

logger = logging.getLogger("merchant-service")


class MerchantService:
    """
    Dedicated service for managing MSME merchant tenants and configuration.
    Strictly tenant-scoped by merchant_id (E.164 phone number).
    """

    def __init__(
        self,
        merchants_store: Dict[str, Merchant],
        parties_store: Optional[Dict[str, Dict[str, Any]]] = None,
        inventory_store: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self.merchants = merchants_store
        self.parties = parties_store if parties_store is not None else {}
        self.inventory = inventory_store if inventory_store is not None else {}

    def setup_or_update_merchant(
        self,
        merchant_id: str,
        business_name: str,
        owner_name: str,
        preferred_lang: str = "hi",
        gstin: Optional[str] = None,
        udyam_reg_no: Optional[str] = None,
        business_address: Optional[str] = None,
        upi_vpa: Optional[str] = None,
        high_credit_threshold: float = 5000.0,
        allow_negative_stock: bool = True,
        whatsapp_phone_number_id: Optional[str] = None,
    ) -> Tuple[Merchant, str]:
        """
        Registers or updates an MSME merchant profile with business details.
        Returns the Merchant model and a formatted WhatsApp confirmation.
        """
        merchant = self.merchants.get(merchant_id)
        if not merchant:
            merchant = Merchant(
                merchant_id=merchant_id,
                business_name=business_name.strip(),
                owner_name=owner_name.strip(),
                preferred_lang=preferred_lang,
                gstin=gstin.strip() if gstin else None,
                udyam_reg_no=udyam_reg_no.strip() if udyam_reg_no else None,
                business_address=business_address.strip() if business_address else None,
                upi_vpa=upi_vpa.strip() if upi_vpa else None,
                high_credit_threshold=high_credit_threshold,
                allow_negative_stock=allow_negative_stock,
                whatsapp_phone_number_id=whatsapp_phone_number_id,
                whatsapp_verified_at=datetime.now(timezone.utc),
                onboarding_status="COMPLETED",
                created_at=datetime.now(timezone.utc),
            )
            self.merchants[merchant_id] = merchant
            action_desc = "व्यवसाय सफलतापूर्वक दर्ज किया गया (Business Registered)"
        else:
            merchant.business_name = business_name.strip()
            merchant.owner_name = owner_name.strip()
            merchant.preferred_lang = preferred_lang
            if gstin:
                merchant.gstin = gstin.strip()
            if udyam_reg_no:
                merchant.udyam_reg_no = udyam_reg_no.strip()
            if business_address:
                merchant.business_address = business_address.strip()
            if upi_vpa:
                merchant.upi_vpa = upi_vpa.strip()
            merchant.high_credit_threshold = high_credit_threshold
            merchant.allow_negative_stock = allow_negative_stock
            merchant.onboarding_status = "COMPLETED"
            action_desc = "व्यवसाय प्रोफाइल अपडेट हुआ (Profile Updated)"

        # Initialize tenant collections if missing
        if merchant_id not in self.parties:
            self.parties[merchant_id] = {}
        if merchant_id not in self.inventory:
            self.inventory[merchant_id] = {}

        formatted_msg = (
            f"🏢 *{action_desc}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ *Business Name:* {merchant.business_name}\n"
            f"👤 *Owner:* {merchant.owner_name}\n"
            f"📞 *Registered Phone:* {merchant.merchant_id}\n"
            f"🌐 *Language:* {merchant.preferred_lang.upper()}\n"
            f"📍 *Address:* {merchant.business_address or 'Not specified'}\n"
            f"🧾 *GSTIN:* {merchant.gstin or 'Non-GST MSME'}\n"
            f"📜 *Udyam No:* {merchant.udyam_reg_no or 'N/A'}\n"
            f"💳 *UPI VPA:* {merchant.upi_vpa or 'Not set'}\n"
            f"🛡️ *Credit Safety Gate:* ₹{merchant.high_credit_threshold:,.2f}\n"
            f"📦 *Negative Stock Policy:* {'Allowed (Warning)' if merchant.allow_negative_stock else 'Strict Block'}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Aapka account sakriya hai! Aap WhatsApp par voice ya text se inventory aur sales log kar sakte hain."
        )

        return merchant, formatted_msg

    def update_merchant_field(
        self,
        merchant_id: str,
        field_name: str,
        value: Any,
    ) -> Tuple[Optional[Merchant], str]:
        """
        Updates a specific configuration field for an existing merchant.
        """
        merchant = self.merchants.get(merchant_id)
        if not merchant:
            return None, "⚠️ *Merchant Not Found*\nPehle apna business register karein (e.g. 'Setup business Sri Balaji Textiles, owner Rajesh')."

        valid_fields = {
            "upi_vpa",
            "business_address",
            "gstin",
            "udyam_reg_no",
            "business_name",
            "owner_name",
            "preferred_lang",
            "high_credit_threshold",
            "allow_negative_stock",
        }

        if field_name not in valid_fields:
            return merchant, f"⚠️ Unknown setting: `{field_name}`. Available: {', '.join(sorted(valid_fields))}."

        if field_name == "high_credit_threshold":
            try:
                setattr(merchant, field_name, float(value))
            except (ValueError, TypeError):
                return merchant, "⚠️ Invalid number for credit threshold."
        elif field_name == "allow_negative_stock":
            if isinstance(value, str):
                setattr(merchant, field_name, value.lower() in ("true", "yes", "allow", "haan"))
            else:
                setattr(merchant, field_name, bool(value))
        else:
            setattr(merchant, field_name, str(value).strip())

        return merchant, (
            f"✅ *Setting Updated / सेटिंग अपडेट हुई*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔧 *Field:* `{field_name}`\n"
            f"✨ *New Value:* {getattr(merchant, field_name)}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

    def get_merchant_profile(self, merchant_id: str) -> Dict[str, Any]:
        """
        Pure read-only query returning formatted MSME profile summary.
        Guarantees zero mutations.
        """
        merchant = self.merchants.get(merchant_id)
        if not merchant:
            return {
                "exists": False,
                "merchant": None,
                "formatted_text": "⚠️ *No Profile Found*\nAapka account registered nahi hai. Likhein: 'Setup business [Name], owner [Owner]' shuru karne ke liye.",
            }

        parties_count = len(self.parties.get(merchant_id, {}))
        inventory_count = len(self.inventory.get(merchant_id, {}))

        formatted = (
            f"🏢 *MSME Business Profile (खाता प्रोफाइल)*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ *Business:* {merchant.business_name}\n"
            f"👤 *Owner:* {merchant.owner_name}\n"
            f"📞 *WhatsApp Phone:* {merchant.merchant_id}\n"
            f"🌐 *Language:* {merchant.preferred_lang.upper()}\n"
            f"📍 *Premises:* {merchant.business_address or 'Not set'}\n"
            f"🧾 *GSTIN:* {merchant.gstin or 'Non-GST Commercial MSME'}\n"
            f"📜 *Udyam:* {merchant.udyam_reg_no or 'N/A'}\n"
            f"💳 *UPI ID:* {merchant.upi_vpa or 'Not configured'}\n"
            f"🛡️ *Safety Gate Limit:* ₹{merchant.high_credit_threshold:,.2f}\n"
            f"📦 *Negative Stock:* {'Allowed with warning' if merchant.allow_negative_stock else 'Blocked'}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👥 *Registered Parties/Customers:* {parties_count}\n"
            f"📦 *Catalog Items (SKUs):* {inventory_count}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        return {
            "exists": True,
            "merchant": merchant,
            "parties_count": parties_count,
            "inventory_count": inventory_count,
            "formatted_text": formatted,
        }
