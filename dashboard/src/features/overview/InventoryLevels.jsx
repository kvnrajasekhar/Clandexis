import React from 'react';
import { Package } from 'lucide-react';

export default function InventoryLevels({ inventory = [] }) {
  return (
    <div className="space-y-4 pt-2">
      <h2 className="text-sm font-bold text-white flex items-center gap-2">
        <Package className="w-4 h-4 text-teal-400" />
        Live Inventory Levels
      </h2>
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
        {inventory.length === 0 ? (
          <div className="p-4 text-center text-xs text-slate-500">
            No inventory items registered yet.
          </div>
        ) : (
          inventory.map((item) => (
            <div
              key={item.sku_id}
              className="p-2 rounded-lg bg-slate-950/60 border border-slate-800 flex justify-between items-center text-xs"
            >
              <div>
                <div className="font-medium text-white">{item.item_name}</div>
                <div className="text-[10px] text-slate-400">
                  Default: ₹{item.default_unit_price} / {item.unit}
                </div>
              </div>
              <div className="text-right">
                <div
                  className={`font-bold text-sm ${
                    item.below_reorder_level ? 'text-rose-400' : 'text-emerald-400'
                  }`}
                >
                  {item.current_stock}{' '}
                  <span className="text-[10px] font-normal text-slate-400">{item.unit}</span>
                </div>
                {item.below_reorder_level && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20">
                    Low Stock (Reorder: {item.reorder_level})
                  </span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
