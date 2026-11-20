"""
Read-Only Conversational Inventory Analytics Engine for Clandexis (FR-01.4 & FR-01.5).
Guarantees strict read-only execution with zero database mutations or side-effects.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from models.firestore_schema import InventoryItem, InventoryMovement, MovementType, Transaction, TransactionStatus


class InventoryAnalytics:
    """
    Read-only analytics service.
    All methods strictly inspect state and return pure data / WhatsApp formatted strings.
    """

    def __init__(
        self,
        inventory_store: Dict[str, Dict[str, InventoryItem]],
        movements_store: Dict[str, List[InventoryMovement]],
        transactions_store: Optional[Dict[str, Transaction]] = None,
    ):
        self._inventory = inventory_store
        self._movements = movements_store
        self._transactions = transactions_store if transactions_store is not None else {}

    # ==========================================================================
    # 1. Stock Status & Valuation (FR-01.4)
    # ==========================================================================

    def get_stock_status(
        self,
        merchant_id: str,
        sku_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pure read-only query of stock levels and inventory valuation.
        """
        items_map = self._inventory.get(merchant_id, {})
        if not items_map:
            return {
                "total_skus": 0,
                "total_valuation": 0.0,
                "items": [],
                "formatted_text": "📦 *Inventory Status (स्टॉक स्थिति)*\nNo items registered in your catalog.",
            }

        if sku_id and sku_id in items_map:
            target_items = [items_map[sku_id]]
        else:
            target_items = list(items_map.values())

        total_valuation = 0.0
        item_details = []
        lines = ["📦 *Current Stock Status (स्टॉक स्थिति)*\n━━━━━━━━━━━━━━━━━━━"]

        for item in sorted(target_items, key=lambda x: x.item_name):
            val = item.current_stock * item.default_unit_price
            total_valuation += val
            low_stock_badge = " ⚠️ *[Low Stock]*" if item.current_stock <= item.reorder_level else ""
            item_details.append({
                "sku_id": item.sku_id,
                "item_name": item.item_name,
                "current_stock": item.current_stock,
                "unit": item.unit,
                "default_unit_price": item.default_unit_price,
                "valuation": val,
                "reorder_level": item.reorder_level,
                "is_low_stock": item.current_stock <= item.reorder_level,
            })
            lines.append(
                f"• *{item.item_name}:* {item.current_stock:.0f} {item.unit} "
                f"(@ ₹{item.default_unit_price:,.2f}) — ₹{val:,.2f}{low_stock_badge}"
            )

        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 *Total Inventory Valuation:* ₹{total_valuation:,.2f}")
        lines.append(f"🏷️ *Total Active SKUs:* {len(target_items)}")

        return {
            "total_skus": len(target_items),
            "total_valuation": total_valuation,
            "items": item_details,
            "formatted_text": "\n".join(lines),
        }

    # ==========================================================================
    # 2. Low Stock Alerts & Reorder Thresholds (FR-01.4)
    # ==========================================================================

    def get_low_stock_alerts(
        self,
        merchant_id: str,
    ) -> Dict[str, Any]:
        """
        Pure read-only query identifying items below their reorder threshold.
        """
        items_map = self._inventory.get(merchant_id, {})
        low_items = [
            it for it in items_map.values()
            if it.current_stock <= it.reorder_level
        ]

        if not low_items:
            return {
                "count": 0,
                "items": [],
                "formatted_text": (
                    "✅ *Stock Health: Good (स्टॉक सुरक्षित)*\n"
                    "All items are above their reorder thresholds. No immediate reordering required!"
                ),
            }

        lines = [
            "⚠️ *Low Stock Alert (पुनः आपूर्ति चेतावनी)*\n"
            "The following items have reached or breached reorder levels:\n"
            "━━━━━━━━━━━━━━━━━━━"
        ]

        item_list = []
        for it in sorted(low_items, key=lambda x: x.current_stock):
            deficit = max(0.0, it.reorder_level - it.current_stock)
            item_list.append({
                "sku_id": it.sku_id,
                "item_name": it.item_name,
                "current_stock": it.current_stock,
                "reorder_level": it.reorder_level,
                "unit": it.unit,
                "deficit": deficit,
            })
            lines.append(
                f"• 🔴 *{it.item_name}:* {it.current_stock:.0f} {it.unit} remaining "
                f"(Threshold: {it.reorder_level:.0f} {it.unit})"
            )

        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 *Tip:* Send production note (e.g. _\"Aaj 50 cotton shirts banayi\"_) to update stock.")

        return {
            "count": len(low_items),
            "items": item_list,
            "formatted_text": "\n".join(lines),
        }

    # ==========================================================================
    # 3. Production Summary (FR-01.4)
    # ==========================================================================

    def get_production_summary(
        self,
        merchant_id: str,
        period: str = "today",
    ) -> Dict[str, Any]:
        """
        Aggregates units manufactured in the specified time period.
        """
        movements = self._movements.get(merchant_id, [])
        now = datetime.now(timezone.utc)

        if period == "today":
            cutoff = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)
            period_label = "Today's Production (आज का उत्पादन)"
        elif period == "week":
            cutoff = now - timedelta(days=7)
            period_label = "Last 7 Days Production (इस हफ्ते का उत्पादन)"
        else:
            cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc)
            period_label = "All-Time Production (कुल उत्पादन)"

        prod_moves = [
            m for m in movements
            if m.movement_type == MovementType.PRODUCTION_ADD
            and m.created_at >= cutoff
        ]

        sku_totals: Dict[str, float] = {}
        total_units = 0.0

        for m in prod_moves:
            sku_totals[m.sku_id] = sku_totals.get(m.sku_id, 0.0) + m.quantity
            total_units += m.quantity

        items_map = self._inventory.get(merchant_id, {})

        if not prod_moves:
            return {
                "total_units": 0.0,
                "item_totals": {},
                "formatted_text": (
                    f"🏭 *{period_label}*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"No manufacturing entries recorded for this period.\n\n"
                    f"To record production, send: _\"Aaj 100 cotton shirts banayi\"_"
                ),
            }

        lines = [f"🏭 *{period_label}*\n━━━━━━━━━━━━━━━━━━━"]
        breakdown = []
        for sku, qty in sku_totals.items():
            name = items_map.get(sku).item_name if sku in items_map else sku
            unit = items_map.get(sku).unit if sku in items_map else "units"
            lines.append(f"• *{name}:* +{qty:.0f} {unit}")
            breakdown.append({"sku_id": sku, "item_name": name, "quantity": qty, "unit": unit})

        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📦 *Total Output:* {total_units:.0f} units ({len(prod_moves)} batches)")

        return {
            "total_units": total_units,
            "batch_count": len(prod_moves),
            "item_totals": breakdown,
            "formatted_text": "\n".join(lines),
        }

    # ==========================================================================
    # 4. Production vs. Sales Comparison (FR-01.4)
    # ==========================================================================

    def get_production_vs_sales(
        self,
        merchant_id: str,
        period: str = "today",
    ) -> Dict[str, Any]:
        """
        Pure read-only comparison between manufacturing output and sales volume.
        """
        movements = self._movements.get(merchant_id, [])
        now = datetime.now(timezone.utc)

        if period == "today":
            cutoff = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)
            period_title = "Today's Production vs. Sales (आज का उत्पादन बनाम बिक्री)"
        else:
            cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc)
            period_title = "All-Time Production vs. Sales (कुल उत्पादन बनाम बिक्री)"

        filtered_moves = [m for m in movements if m.created_at >= cutoff]

        produced_by_sku: Dict[str, float] = {}
        sold_by_sku: Dict[str, float] = {}
        damaged_by_sku: Dict[str, float] = {}

        total_produced = 0.0
        total_sold = 0.0
        total_damaged = 0.0

        for m in filtered_moves:
            if m.movement_type == MovementType.PRODUCTION_ADD:
                produced_by_sku[m.sku_id] = produced_by_sku.get(m.sku_id, 0.0) + m.quantity
                total_produced += m.quantity
            elif m.movement_type == MovementType.SALE_DEDUCT:
                qty = abs(m.quantity)
                sold_by_sku[m.sku_id] = sold_by_sku.get(m.sku_id, 0.0) + qty
                total_sold += qty
            elif m.movement_type in (MovementType.DAMAGE_WASTAGE, MovementType.ADJUSTMENT) and m.quantity < 0:
                qty = abs(m.quantity)
                damaged_by_sku[m.sku_id] = damaged_by_sku.get(m.sku_id, 0.0) + qty
                total_damaged += qty

        items_map = self._inventory.get(merchant_id, {})
        all_skus = set(produced_by_sku.keys()).union(sold_by_sku.keys()).union(damaged_by_sku.keys())

        lines = [f"📊 *{period_title}*\n━━━━━━━━━━━━━━━━━━━"]

        if not all_skus:
            lines.append("No production or sales activity recorded for this period.")
        else:
            for sku in sorted(all_skus):
                name = items_map.get(sku).item_name if sku in items_map else sku
                unit = items_map.get(sku).unit if sku in items_map else "units"
                p = produced_by_sku.get(sku, 0.0)
                s = sold_by_sku.get(sku, 0.0)
                d = damaged_by_sku.get(sku, 0.0)
                net = p - s - d
                lines.append(
                    f"• *{name}:*\n"
                    f"   🏭 Made: +{p:.0f} {unit} | 🛒 Sold: -{s:.0f} {unit}\n"
                    f"   📉 Net Delta: {'+' if net >= 0 else ''}{net:.0f} {unit}"
                )

        lines.append("━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🏭 *Total Produced:* +{total_produced:.0f} units")
        lines.append(f"🛒 *Total Sold:* -{total_sold:.0f} units")
        if total_damaged > 0:
            lines.append(f"⚠️ *Damaged/Wastage:* -{total_damaged:.0f} units")
        lines.append(f"📦 *Net Inventory Delta:* {'+' if (total_produced - total_sold - total_damaged) >= 0 else ''}{total_produced - total_sold - total_damaged:.0f} units")

        return {
            "period": period,
            "total_produced": total_produced,
            "total_sold": total_sold,
            "total_damaged": total_damaged,
            "net_delta": total_produced - total_sold - total_damaged,
            "formatted_text": "\n".join(lines),
        }

    # ==========================================================================
    # 5. Item Movement Audit History (FR-01.3 & FR-01.4)
    # ==========================================================================

    def get_movement_history(
        self,
        merchant_id: str,
        sku_id: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """
        Pure read-only retrieval of the chronological audit trail for a SKU.
        """
        movements = [
            m for m in self._movements.get(merchant_id, [])
            if m.sku_id == sku_id
        ]
        items_map = self._inventory.get(merchant_id, {})
        item_name = items_map.get(sku_id).item_name if sku_id in items_map else sku_id

        if not movements:
            return {
                "sku_id": sku_id,
                "history": [],
                "formatted_text": f"📜 *Movement History for {item_name}*\nNo movements recorded yet.",
            }

        sorted_moves = sorted(movements, key=lambda x: x.created_at, reverse=True)[:limit]
        lines = [f"📜 *Recent Movements for {item_name}*\n━━━━━━━━━━━━━━━━━━━"]

        history_items = []
        for m in sorted_moves:
            sign = "+" if m.quantity > 0 else ""
            date_str = m.created_at.strftime("%d %b %H:%M")
            lines.append(
                f"• [{date_str}] *{m.movement_type.value}:* {sign}{m.quantity:.0f} "
                f"→ Balance: {m.balance_after:.0f}"
            )
            history_items.append({
                "movement_id": m.movement_id,
                "timestamp": m.created_at.isoformat(),
                "type": m.movement_type.value,
                "quantity": m.quantity,
                "balance_after": m.balance_after,
                "notes": getattr(m, "notes", None),
            })

        lines.append("━━━━━━━━━━━━━━━━━━━")

        return {
            "sku_id": sku_id,
            "history": history_items,
            "formatted_text": "\n".join(lines),
        }
