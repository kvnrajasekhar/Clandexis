"""
Inventory Management Service for Clandexis Invisible ERP (FR-01).
Handles catalog setup, fuzzy SKU resolution, append-only production logs,
and stock adjustments with multi-tenant isolation and policy enforcement.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from models.firestore_schema import (
    InventoryItem,
    InventoryMovement,
    Merchant,
    MovementType,
)

logger = logging.getLogger("inventory-service")


class InventoryService:
    """
    Manages tenant inventory catalogs and mutations.
    Operates on tenant-isolated state (in-memory, syncable with Firestore/Cloud SQL).
    """

    def __init__(
        self,
        inventory_store: Optional[Dict[str, Dict[str, InventoryItem]]] = None,
        movements_store: Optional[Dict[str, List[InventoryMovement]]] = None,
        merchants_store: Optional[Dict[str, Merchant]] = None,
    ):
        # merchant_id -> {sku_id: InventoryItem}
        self.inventory: Dict[str, Dict[str, InventoryItem]] = inventory_store if inventory_store is not None else {}
        # merchant_id -> [InventoryMovement]
        self.movements: Dict[str, List[InventoryMovement]] = movements_store if movements_store is not None else {}
        # merchant_id -> Merchant
        self.merchants: Dict[str, Merchant] = merchants_store if merchants_store is not None else {}

    # ==========================================================================
    # Catalog Setup & Management (FR-01)
    # ==========================================================================

    def create_or_update_item(
        self,
        merchant_id: str,
        item_name: str,
        unit: str = "pieces",
        default_unit_price: float = 0.0,
        initial_stock: float = 0.0,
        reorder_level: float = 20.0,
        aliases: Optional[List[str]] = None,
        sku_id: Optional[str] = None,
    ) -> Tuple[InventoryItem, bool]:
        """
        Creates or updates a catalog item for the merchant.
        Returns (item, is_new).
        """
        if merchant_id not in self.inventory:
            self.inventory[merchant_id] = {}

        if not sku_id:
            # Generate deterministic or slug-based SKU ID
            clean_slug = re.sub(r"[^a-zA-Z0-9]+", "-", item_name.strip().lower()).strip("-")
            sku_id = clean_slug or f"sku-{uuid.uuid4().hex[:6]}"

        clean_aliases = [a.strip().lower() for a in (aliases or []) if a.strip()]
        # Add basic name variations to aliases
        name_lower = item_name.strip().lower()
        if name_lower not in clean_aliases:
            clean_aliases.append(name_lower)

        is_new = sku_id not in self.inventory[merchant_id]

        if is_new:
            item = InventoryItem(
                sku_id=sku_id,
                item_name=item_name.strip(),
                aliases=clean_aliases,
                unit=unit.strip().lower(),
                current_stock=initial_stock,
                default_unit_price=default_unit_price,
                reorder_level=reorder_level,
                updated_at=datetime.now(timezone.utc),
            )
            self.inventory[merchant_id][sku_id] = item

            # If initial stock was provided, record initial balance movement
            if initial_stock > 0:
                movement = InventoryMovement(
                    movement_id=f"mov_{uuid.uuid4().hex[:8]}",
                    merchant_id=merchant_id,
                    sku_id=sku_id,
                    movement_type=MovementType.PRODUCTION_ADD,
                    quantity=initial_stock,
                    balance_after=initial_stock,
                    reference_txn_id=f"init_{uuid.uuid4().hex[:6]}",
                    created_at=datetime.now(timezone.utc),
                )
                if merchant_id not in self.movements:
                    self.movements[merchant_id] = []
                self.movements[merchant_id].append(movement)

            logger.info(f"Registered new SKU '{sku_id}' for merchant {merchant_id}.")
        else:
            item = self.inventory[merchant_id][sku_id]
            item.item_name = item_name.strip()
            item.default_unit_price = default_unit_price if default_unit_price > 0 else item.default_unit_price
            item.unit = unit.strip().lower()
            item.reorder_level = reorder_level
            for alias in clean_aliases:
                if alias not in item.aliases:
                    item.aliases.append(alias)
            item.updated_at = datetime.now(timezone.utc)
            logger.info(f"Updated SKU '{sku_id}' for merchant {merchant_id}.")

        return item, is_new

    # ==========================================================================
    # Fuzzy SKU Resolution (FR-01.2)
    # ==========================================================================

    def resolve_sku(
        self,
        merchant_id: str,
        query: str,
    ) -> Tuple[Optional[str], Optional[InventoryItem], float]:
        """
        Fuzzy matches a user-spoken query or product name against merchant catalog.
        Returns (sku_id, item, confidence_score).
        """
        items = self.inventory.get(merchant_id, {})
        if not items:
            return None, None, 0.0

        q = query.strip().lower()
        if not q:
            return None, None, 0.0

        # 1. Exact match on SKU ID
        if q in items:
            return q, items[q], 1.0

        # 2. Exact match on item_name (case-insensitive)
        for sku, item in items.items():
            if item.item_name.lower() == q:
                return sku, item, 1.0

        # 3. Exact match in aliases
        for sku, item in items.items():
            if any(alias.lower() == q for alias in item.aliases):
                return sku, item, 0.98

        # 4. Token overlap & substring match
        best_match: Optional[Tuple[str, InventoryItem, float]] = None
        q_tokens = set(re.findall(r"\b\w+\b", q))

        for sku, item in items.items():
            # Check item name substring
            if q in item.item_name.lower() or item.item_name.lower() in q:
                score = 0.90
                if not best_match or score > best_match[2]:
                    best_match = (sku, item, score)
                continue

            # Check alias substring
            for alias in item.aliases:
                if alias in q or q in alias:
                    score = 0.88
                    if not best_match or score > best_match[2]:
                        best_match = (sku, item, score)
                    break

            # Token overlap score
            item_tokens = set(re.findall(r"\b\w+\b", item.item_name.lower()))
            for alias in item.aliases:
                item_tokens.update(re.findall(r"\b\w+\b", alias.lower()))

            overlap = q_tokens.intersection(item_tokens)
            if overlap:
                score = 0.65 + (0.10 * len(overlap))
                score = min(0.84, score)  # Bound fuzzy token overlap below auto-confirm threshold
                if not best_match or score > best_match[2]:
                    best_match = (sku, item, score)

        if best_match and best_match[2] >= 0.70:
            return best_match

        return None, None, 0.0

    # ==========================================================================
    # Controlled Inventory Mutations (FR-01.3 & FR-02)
    # ==========================================================================

    def record_production(
        self,
        merchant_id: str,
        sku_id: str,
        quantity: float,
        batch_number: Optional[str] = None,
        notes: Optional[str] = None,
        reference_txn_id: Optional[str] = None,
    ) -> Tuple[InventoryItem, InventoryMovement]:
        """
        Records manufacturing/production output.
        Atomically increments current_stock and appends InventoryMovement.
        """
        if quantity <= 0:
            raise ValueError(f"Production quantity must be positive, got {quantity}")

        items = self.inventory.get(merchant_id, {})
        if sku_id not in items:
            raise KeyError(f"SKU '{sku_id}' not found in merchant catalog.")

        item = items[sku_id]
        item.current_stock += quantity
        item.updated_at = datetime.now(timezone.utc)

        movement_id = f"mov_{uuid.uuid4().hex[:8]}"
        note_str = f"Batch: {batch_number}" if batch_number else (notes or "Daily Production")
        movement = InventoryMovement(
            movement_id=movement_id,
            merchant_id=merchant_id,
            sku_id=sku_id,
            movement_type=MovementType.PRODUCTION_ADD,
            quantity=quantity,
            balance_after=item.current_stock,
            reference_txn_id=reference_txn_id or f"prod_{uuid.uuid4().hex[:6]}",
            created_at=datetime.now(timezone.utc),
        )

        if merchant_id not in self.movements:
            self.movements[merchant_id] = []
        self.movements[merchant_id].append(movement)

        logger.info(
            f"Logged production +{quantity} {item.unit} for '{sku_id}'. New balance: {item.current_stock}."
        )
        return item, movement

    def record_adjustment(
        self,
        merchant_id: str,
        sku_id: str,
        quantity_delta: float,
        reason: str = "DAMAGE_WASTAGE",
        notes: Optional[str] = None,
        reference_txn_id: Optional[str] = None,
    ) -> Tuple[InventoryItem, InventoryMovement, Optional[str]]:
        """
        Records wastage, damage, shrinkage, or audit adjustments.
        Negative quantity_delta reduces stock; positive increments it.
        Returns (item, movement, warning_message).
        """
        items = self.inventory.get(merchant_id, {})
        if sku_id not in items:
            raise KeyError(f"SKU '{sku_id}' not found in merchant catalog.")

        item = items[sku_id]
        new_balance = item.current_stock + quantity_delta

        # Check negative stock policy
        warning: Optional[str] = None
        merchant = self.merchants.get(merchant_id)
        allow_neg = merchant.allow_negative_stock if merchant else True

        if new_balance < 0:
            if not allow_neg:
                raise ValueError(
                    f"Insufficient stock for SKU '{sku_id}'. Current: {item.current_stock}, Adjustment: {quantity_delta}"
                )
            warning = f"⚠️ Stock fell below zero ({new_balance:.0f} {item.unit}) due to adjustment."

        item.current_stock = new_balance
        item.updated_at = datetime.now(timezone.utc)

        # Map movement type
        mov_type = MovementType.DAMAGE_WASTAGE if "damage" in reason.lower() or "waste" in reason.lower() else MovementType.AUDIT_ADJUSTMENT

        movement_id = f"mov_{uuid.uuid4().hex[:8]}"
        movement = InventoryMovement(
            movement_id=movement_id,
            merchant_id=merchant_id,
            sku_id=sku_id,
            movement_type=mov_type,
            quantity=quantity_delta,
            balance_after=item.current_stock,
            reference_txn_id=reference_txn_id or f"adj_{uuid.uuid4().hex[:6]}",
            created_at=datetime.now(timezone.utc),
        )

        if merchant_id not in self.movements:
            self.movements[merchant_id] = []
        self.movements[merchant_id].append(movement)

        logger.info(
            f"Logged adjustment {quantity_delta:+} {item.unit} for '{sku_id}'. New balance: {item.current_stock}."
        )
        return item, movement, warning

    def record_sale_deduction(
        self,
        merchant_id: str,
        sku_id: str,
        quantity: float,
        reference_txn_id: Optional[str] = None,
    ) -> Tuple[InventoryItem, InventoryMovement, Optional[str]]:
        """
        Deducts sold stock during committed sales transaction (FR-02.1).
        """
        if quantity <= 0:
            raise ValueError(f"Deduction quantity must be positive, got {quantity}")

        items = self.inventory.get(merchant_id, {})
        if sku_id not in items:
            # Create a placeholder on-the-fly if missing to avoid unrecoverable failure
            item, _ = self.create_or_update_item(merchant_id, item_name=sku_id, sku_id=sku_id)
        else:
            item = items[sku_id]

        new_balance = item.current_stock - quantity
        warning = None
        merchant = self.merchants.get(merchant_id)
        allow_neg = merchant.allow_negative_stock if merchant else True

        if new_balance < 0:
            if not allow_neg:
                raise ValueError(
                    f"Insufficient stock for SKU '{sku_id}'. Available: {item.current_stock}, Requested: {quantity}"
                )
            warning = f"⚠️ Low/Negative stock warning: Current balance is {new_balance:.0f} {item.unit}."

        item.current_stock = new_balance
        item.updated_at = datetime.now(timezone.utc)

        movement_id = f"mov_{uuid.uuid4().hex[:8]}"
        movement = InventoryMovement(
            movement_id=movement_id,
            merchant_id=merchant_id,
            sku_id=sku_id,
            movement_type=MovementType.SALE_DEDUCT,
            quantity=-quantity,
            balance_after=item.current_stock,
            reference_txn_id=reference_txn_id or f"sale_{uuid.uuid4().hex[:6]}",
            created_at=datetime.now(timezone.utc),
        )

        if merchant_id not in self.movements:
            self.movements[merchant_id] = []
        self.movements[merchant_id].append(movement)

        return item, movement, warning
