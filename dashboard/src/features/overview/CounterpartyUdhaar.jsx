import React from 'react';
import { CreditCard } from 'lucide-react';

export default function CounterpartyUdhaar({ parties = [] }) {
  return (
    <div className="space-y-4">
      <h2 className="text-sm font-bold text-white flex items-center gap-2">
        <CreditCard className="w-4 h-4 text-emerald-400" />
        Counterparty Udhaar Balances
      </h2>

      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2.5">
        {parties.length === 0 ? (
          <div className="text-center p-4 text-xs text-slate-400">No parties found.</div>
        ) : (
          parties.map((p) => (
            <div
              key={p.party_id}
              className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 flex justify-between items-center text-xs"
            >
              <div>
                <div className="font-semibold text-white">{p.display_name}</div>
                <div className="text-[11px] text-slate-400">{p.phone || 'No phone'}</div>
              </div>
              <div className="text-right">
                <div
                  className={`font-bold ${
                    p.current_udhaar_balance > 0 ? 'text-amber-400' : 'text-slate-400'
                  }`}
                >
                  ₹{Number(p.current_udhaar_balance).toFixed(2)}
                </div>
                <div className="text-[10px] text-slate-500">Limit: ₹{p.credit_limit}</div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
