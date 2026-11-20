import React from 'react';
import { TrendingUp, Wallet, CreditCard, ShieldCheck } from 'lucide-react';

export default function BusinessPulseCards({ summary, partyCount = 0 }) {
  const salesTotal = summary ? summary.sales_total.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';
  const cashAndUpi = summary ? (summary.cash_received + summary.upi_received).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';
  const creditExtended = summary ? summary.credit_extended.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';
  const totalReceivables = summary ? summary.total_receivables_outstanding.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
      {/* Metric 1: Today's Sales */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-3.5 sm:p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
          <span>Today's Sales</span>
          <div className="p-1.5 sm:p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
            <TrendingUp className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2.5 sm:mt-3">
          <div className="text-xl sm:text-2xl font-bold text-white tracking-tight">
            ₹{salesTotal}
          </div>
          <div className="text-xs text-slate-400 mt-1 flex items-center gap-1">
            <span className="text-emerald-400 font-semibold">{summary ? summary.sales_count : 0}</span> sales transactions
          </div>
        </div>
      </div>

      {/* Metric 2: Cash & UPI Received */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-3.5 sm:p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
          <span>Cash & UPI Collected</span>
          <div className="p-1.5 sm:p-2 rounded-lg bg-teal-500/10 text-teal-400">
            <Wallet className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2.5 sm:mt-3">
          <div className="text-xl sm:text-2xl font-bold text-white tracking-tight">
            ₹{cashAndUpi}
          </div>
          <div className="text-xs text-slate-400 mt-1 truncate">
            Cash: ₹{summary ? summary.cash_received : 0} • UPI: ₹{summary ? summary.upi_received : 0}
          </div>
        </div>
      </div>

      {/* Metric 3: Today's Credit (Udhaar) */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-3.5 sm:p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
          <span>Credit Issued Today</span>
          <div className="p-1.5 sm:p-2 rounded-lg bg-amber-500/10 text-amber-400">
            <CreditCard className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2.5 sm:mt-3">
          <div className="text-xl sm:text-2xl font-bold text-amber-400 tracking-tight">
            ₹{creditExtended}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Collections: ₹{summary ? summary.collections_total : 0}
          </div>
        </div>
      </div>

      {/* Metric 4: Total AR Outstanding */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-3.5 sm:p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
          <span>Total Udhaar Outstanding</span>
          <div className="p-1.5 sm:p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
            <ShieldCheck className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2.5 sm:mt-3">
          <div className="text-xl sm:text-2xl font-bold text-white tracking-tight">
            ₹{totalReceivables}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Across {partyCount} counterparties
          </div>
        </div>
      </div>
    </div>
  );
}
