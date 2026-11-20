import React from 'react';
import { FileText } from 'lucide-react';
import Badge from '../../components/common/Badge';

export default function TransactionsTab({ transactions = [] }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-white">Committed Financial Ledger Entries</h2>
        <span className="text-xs text-slate-400">Total: {transactions.length} transactions</span>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-slate-800/80 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
              <tr>
                <th className="p-3">Txn ID</th>
                <th className="p-3">Date</th>
                <th className="p-3">Intent</th>
                <th className="p-3">Party</th>
                <th className="p-3">Total (₹)</th>
                <th className="p-3">Credit (₹)</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {transactions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-400">
                    No committed transactions recorded yet in the ledger.
                  </td>
                </tr>
              ) : (
                transactions.map((t) => (
                  <tr key={t.txn_id} className="hover:bg-slate-800/40 transition">
                    <td className="p-3 font-mono text-[11px] text-slate-400">
                      <div>{t.txn_id}</div>
                      {t.intent === 'SALE_INVOICE' && (
                        <a
                          className="inline-flex items-center gap-1 underline text-sky-400 hover:text-sky-300 mt-1 font-sans text-[11px]"
                          href={`/api/v1/dashboard/transactions/${encodeURIComponent(t.txn_id)}/invoice`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <FileText className="w-3 h-3" /> Download invoice
                        </a>
                      )}
                    </td>
                    <td className="p-3 whitespace-nowrap">{t.business_date || 'Today'}</td>
                    <td className="p-3 font-semibold text-white whitespace-nowrap">{t.intent}</td>
                    <td className="p-3 font-mono text-[11px] text-slate-300">{t.party_id}</td>
                    <td className="p-3 font-bold text-emerald-400 whitespace-nowrap">
                      ₹{((t.total_minor || 0) / 100).toFixed(2)}
                    </td>
                    <td className="p-3 text-amber-400 whitespace-nowrap">
                      ₹{((t.credit_minor || 0) / 100).toFixed(2)}
                    </td>
                    <td className="p-3">
                      <Badge variant="emerald">{t.status || 'COMMITTED'}</Badge>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
