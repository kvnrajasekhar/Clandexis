import React from 'react';
import { Package } from 'lucide-react';
import Badge from '../../components/common/Badge';

export default function InventoryTab({ inventory = [] }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-white">Master Inventory Catalog & Stock Movements</h2>
        <span className="text-xs text-slate-400">Total Items: {inventory.length}</span>
      </div>

      {inventory.length === 0 ? (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
          <Package className="w-10 h-10 mx-auto mb-2 text-slate-600" />
          <p className="font-semibold text-slate-200">No inventory items registered yet</p>
          <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto">
            Your inventory catalog will automatically populate as products and stock entries are recorded via WhatsApp or voice transactions.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {inventory.map((it) => (
            <div key={it.sku_id} className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2 hover:border-slate-700 transition">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="font-bold text-white text-sm">{it.item_name}</h3>
                  <p className="text-xs text-slate-400 font-mono text-[11px]">SKU: {it.sku_id}</p>
                </div>
                <Badge variant={it.below_reorder_level ? 'rose' : 'emerald'}>
                  {it.below_reorder_level ? 'Low Stock' : 'In Stock'}
                </Badge>
              </div>

              <div className="pt-2 border-t border-slate-800 flex justify-between items-baseline">
                <span className="text-xs text-slate-400">Available:</span>
                <span className="text-xl font-bold text-white">
                  {it.current_stock} <span className="text-xs font-normal text-slate-400">{it.unit}</span>
                </span>
              </div>

              <div className="flex justify-between text-xs text-slate-400">
                <span>Default Price:</span>
                <span className="text-slate-200 font-medium">₹{it.default_unit_price}</span>
              </div>

              <div className="flex justify-between text-xs text-slate-400">
                <span>Reorder Level:</span>
                <span className="text-slate-200 font-medium">{it.reorder_level} {it.unit}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
