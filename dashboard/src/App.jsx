import React, { useState, useEffect } from 'react';
import { 
  TrendingUp, 
  Wallet, 
  CreditCard, 
  Package, 
  AlertTriangle, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  FileText, 
  Mic, 
  Upload, 
  RefreshCw, 
  ShieldCheck,
  ArrowUpRight,
  ArrowDownLeft,
  Building2,
  Phone,
  HelpCircle,
  Eye,
  SlidersHorizontal
} from 'lucide-react';

const MERCHANT_ID = "+919876543210";

export default function App() {
  const [activeTab, setActiveTab] = useState('overview'); // 'overview', 'candidates', 'transactions', 'inventory', 'demo'
  const [summary, setSummary] = useState(null);
  const [inventory, setInventory] = useState([]);
  const [parties, setParties] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionMessage, setActionMessage] = useState(null);

  // Demo Extractor Form state
  const [demoText, setDemoText] = useState("Ramesh ki 50 cotton shirts 200 each ki ammamu. 5000 cash, 5000 udhar.");
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoResult, setDemoResult] = useState(null);

  const fetchData = async () => {
    try {
      setRefreshing(true);
      const [sumRes, invRes, partRes, txnRes, candRes] = await Promise.all([
        fetch(`/api/v1/dashboard/summary?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        fetch(`/api/v1/dashboard/inventory?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        fetch(`/api/v1/dashboard/parties?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        fetch(`/api/v1/dashboard/transactions?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        fetch(`/api/v1/dashboard/candidates?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
      ]);

      if (sumRes.ok) setSummary(await sumRes.json());
      if (invRes.ok) setInventory(await invRes.json());
      if (partRes.ok) setParties(await partRes.json());
      if (txnRes.ok) setTransactions(await txnRes.json());
      if (candRes.ok) setCandidates(await candRes.json());
    } catch (err) {
      console.error("Dashboard fetch error:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 8000);
    return () => clearInterval(interval);
  }, []);

  const handleConfirmCandidate = async (candidateId) => {
    try {
      const res = await fetch('/api/v1/dashboard/candidates/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          merchant_id: MERCHANT_ID,
          candidate_id: candidateId,
          confirmed_by: MERCHANT_ID
        })
      });
      const data = await res.json();
      if (res.ok) {
        setActionMessage({ type: 'success', text: `Transaction committed! Txn ID: ${data.txn_id}` });
        fetchData();
      } else {
        setActionMessage({ type: 'error', text: `Commit failed: ${data.detail || 'Held for review'}` });
      }
    } catch (e) {
      setActionMessage({ type: 'error', text: e.message });
    }
  };

  const handleCancelCandidate = async (candidateId) => {
    try {
      const res = await fetch('/api/v1/dashboard/candidates/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          merchant_id: MERCHANT_ID,
          candidate_id: candidateId,
          confirmed_by: MERCHANT_ID
        })
      });
      if (res.ok) {
        setActionMessage({ type: 'info', text: 'Candidate marked as CANCELLED.' });
        fetchData();
      }
    } catch (e) {
      setActionMessage({ type: 'error', text: e.message });
    }
  };

  const handleDemoExtract = async (e) => {
    e.preventDefault();
    setDemoLoading(true);
    setDemoResult(null);
    try {
      const res = await fetch('/api/v1/extract/text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          merchant_id: MERCHANT_ID,
          message_id: `demo_${Date.now()}`,
          raw_text: demoText,
          merchant_business_name: "Sri Balaji Textiles"
        })
      });
      const data = await res.json();
      setDemoResult(data);
      fetchData();
    } catch (err) {
      setDemoResult({ error: err.message });
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Top Navigation */}
      <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50 px-6 py-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 text-xl shadow-lg shadow-emerald-500/20">
            ⚡
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="font-bold text-lg text-white tracking-tight">Clandexis Invisible ERP</h1>
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Judge Live Dashboard
              </span>
            </div>
            <p className="text-xs text-slate-400 flex items-center gap-1.5 mt-0.5">
              <Building2 className="w-3.5 h-3.5 text-slate-500" /> Sri Balaji Textiles (Rajesh Kumar) • <Phone className="w-3 h-3 text-slate-500" /> {MERCHANT_ID}
            </p>
          </div>
        </div>

        {/* Action Controls & Navigation */}
        <div className="flex items-center space-x-2 sm:space-x-3">
          <div className="bg-slate-800/80 p-1 rounded-lg border border-slate-700/60 flex space-x-1 text-xs font-medium">
            {['overview', 'candidates', 'transactions', 'inventory', 'demo'].map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 rounded-md transition-all capitalize ${
                  activeTab === tab
                    ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                    : 'text-slate-300 hover:text-white hover:bg-slate-700/50'
                }`}
              >
                {tab === 'demo' ? '🧪 Live Extractor' : tab}
              </button>
            ))}
          </div>

          <button
            onClick={fetchData}
            disabled={refreshing}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1 text-xs"
            title="Refresh database state"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-emerald-400' : ''}`} />
          </button>
        </div>
      </header>

      {/* Alert Banner */}
      {actionMessage && (
        <div className={`px-6 py-2.5 text-xs font-medium flex items-center justify-between border-b ${
          actionMessage.type === 'error' ? 'bg-rose-500/10 border-rose-500/20 text-rose-300' :
          actionMessage.type === 'info' ? 'bg-sky-500/10 border-sky-500/20 text-sky-300' :
          'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
        }`}>
          <span>{actionMessage.text}</span>
          <button onClick={() => setActionMessage(null)} className="opacity-70 hover:opacity-100">✕</button>
        </div>
      )}

      {/* Main Content Area */}
      <main className="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">
        
        {/* Daily Business Pulse (Always visible at top of Overview) */}
        {activeTab === 'overview' && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Metric 1: Today's Sales */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
                <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
                  <span>Today's Sales</span>
                  <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
                    <TrendingUp className="w-4 h-4" />
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-2xl font-bold text-white tracking-tight">
                    ₹{summary ? summary.total_sales.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
                  </div>
                  <div className="text-xs text-slate-400 mt-1 flex items-center gap-1">
                    <span className="text-emerald-400 font-semibold">{summary ? summary.sales_count : 0}</span> sales transactions
                  </div>
                </div>
              </div>

              {/* Metric 2: Cash & UPI Received */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
                <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
                  <span>Cash & UPI Collected</span>
                  <div className="p-2 rounded-lg bg-teal-500/10 text-teal-400">
                    <Wallet className="w-4 h-4" />
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-2xl font-bold text-white tracking-tight">
                    ₹{summary ? (summary.cash_received + summary.upi_received).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Cash: ₹{summary ? summary.cash_received : 0} • UPI: ₹{summary ? summary.upi_received : 0}
                  </div>
                </div>
              </div>

              {/* Metric 3: Today's Credit (Udhaar) */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
                <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
                  <span>Credit Issued Today</span>
                  <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400">
                    <CreditCard className="w-4 h-4" />
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-2xl font-bold text-amber-400 tracking-tight">
                    ₹{summary ? summary.credit_issued.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Collections: ₹{summary ? summary.udhaar_collected : 0}
                  </div>
                </div>
              </div>

              {/* Metric 4: Total AR Outstanding */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
                <div className="flex items-center justify-between text-slate-400 text-xs font-medium">
                  <span>Total Udhaar Outstanding</span>
                  <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
                    <ShieldCheck className="w-4 h-4" />
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-2xl font-bold text-white tracking-tight">
                    ₹{summary ? summary.outstanding_ar.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Across {parties.length} counterparties
                  </div>
                </div>
              </div>
            </div>

            {/* Two-Column Layout: Staged Candidates & Parties */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Left 2 Cols: Staged Action Panel (Human-in-the-Loop Safety Gate) */}
              <div className="lg:col-span-2 space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold text-white flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-400" />
                    Pending Confirmations & Review Queue ({candidates.filter(c => c.posting_status !== 'POSTED' && c.posting_status !== 'CANCELLED').length})
                  </h2>
                  <span className="text-xs text-slate-400">Requires explicit merchant confirmation</span>
                </div>

                <div className="space-y-3">
                  {candidates.filter(c => c.posting_status !== 'POSTED' && c.posting_status !== 'CANCELLED').length === 0 ? (
                    <div className="p-8 text-center bg-slate-900/40 border border-slate-800 rounded-xl text-slate-400 text-xs">
                      No pending reviews or unconfirmed transactions. All candidates committed or clear!
                    </div>
                  ) : (
                    candidates
                      .filter(c => c.posting_status !== 'POSTED' && c.posting_status !== 'CANCELLED')
                      .map((cand) => (
                        <div 
                          key={cand.candidate_id} 
                          className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3 relative hover:border-slate-700 transition"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-sm text-white">
                                  {cand.extracted_snapshot?.buyer_name || cand.extracted_snapshot?.party_name || 'Customer'}
                                </span>
                                <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full border ${
                                  cand.posting_status === 'NEEDS_REVIEW' 
                                    ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' 
                                    : 'bg-sky-500/10 text-sky-400 border-sky-500/30'
                                }`}>
                                  {cand.posting_status}
                                </span>
                              </div>
                              <p className="text-xs text-slate-400 mt-0.5">
                                Source Msg: <code className="text-slate-300 font-mono text-[11px]">{cand.source_message_id}</code>
                              </p>
                            </div>
                            <div className="text-right">
                              <span className="text-base font-bold text-white">
                                ₹{cand.extracted_snapshot?.total_amount ? cand.extracted_snapshot.total_amount.toFixed(2) : '0.00'}
                              </span>
                              <p className="text-[11px] text-slate-400">
                                Paid: ₹{cand.extracted_snapshot?.amount_paid || 0} • Due: ₹{cand.extracted_snapshot?.credit_amount || 0}
                              </p>
                            </div>
                          </div>

                          {/* Line items summary */}
                          {cand.extracted_snapshot?.items && (
                            <div className="bg-slate-950/60 rounded-lg p-2.5 text-xs text-slate-300 border border-slate-800/80">
                              <div className="font-medium text-slate-400 text-[11px] mb-1">Items:</div>
                              {cand.extracted_snapshot.items.map((it, idx) => (
                                <div key={idx} className="flex justify-between text-xs">
                                  <span>{it.quantity} {it.item_name} @ ₹{it.unit_price}</span>
                                  <span>₹{it.line_total}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Warnings / Clarifications */}
                          {cand.validation_warnings?.length > 0 && (
                            <div className="text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/20 p-2 rounded-lg">
                              ⚠️ {cand.validation_warnings.join(", ")}
                            </div>
                          )}

                          {cand.extracted_snapshot?.clarification_question && (
                            <div className="text-[11px] text-sky-300 bg-sky-500/10 border border-sky-500/20 p-2 rounded-lg">
                              💬 WhatsApp Question: {cand.extracted_snapshot.clarification_question}
                            </div>
                          )}

                          {/* Safety Gate Decision Actions */}
                          <div className="flex items-center justify-end space-x-2 pt-1 border-t border-slate-800">
                            <button
                              onClick={() => handleCancelCandidate(cand.candidate_id)}
                              className="px-3 py-1 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                            >
                              Reject / Cancel
                            </button>
                            <button
                              onClick={() => handleConfirmCandidate(cand.candidate_id)}
                              className="px-3 py-1 text-xs font-bold rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-sm transition"
                            >
                              ✅ Authorize & Commit to Ledger
                            </button>
                          </div>
                        </div>
                      ))
                  )}
                </div>
              </div>

              {/* Right Col: Counterparties & Credit Limits */}
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
                      <div key={p.party_id} className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 flex justify-between items-center text-xs">
                        <div>
                          <div className="font-semibold text-white">{p.display_name}</div>
                          <div className="text-[11px] text-slate-400">{p.phone || 'No phone'}</div>
                        </div>
                        <div className="text-right">
                          <div className={`font-bold ${p.current_udhaar_balance > 0 ? 'text-amber-400' : 'text-slate-400'}`}>
                            ₹{p.current_udhaar_balance.toFixed(2)}
                          </div>
                          <div className="text-[10px] text-slate-500">Limit: ₹{p.credit_limit}</div>
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Master Inventory Stock Levels */}
                <h2 className="text-sm font-bold text-white flex items-center gap-2 pt-2">
                  <Package className="w-4 h-4 text-teal-400" />
                  Live Inventory Levels
                </h2>
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
                  {inventory.map((item) => (
                    <div key={item.sku_id} className="p-2 rounded-lg bg-slate-950/60 border border-slate-800 flex justify-between items-center text-xs">
                      <div>
                        <div className="font-medium text-white">{item.item_name}</div>
                        <div className="text-[10px] text-slate-400">Default: ₹{item.default_unit_price} / {item.unit}</div>
                      </div>
                      <div className="text-right">
                        <div className={`font-bold text-sm ${item.below_reorder_level ? 'text-rose-400' : 'text-emerald-400'}`}>
                          {item.current_stock} <span className="text-[10px] font-normal text-slate-400">{item.unit}</span>
                        </div>
                        {item.below_reorder_level && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20">
                            Low Stock (Reorder: {item.reorder_level})
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}

        {/* Tab: Candidates Queue */}
        {activeTab === 'candidates' && (
          <div className="space-y-4">
            <h2 className="text-base font-bold text-white">Full Transaction Candidate Staging Queue</h2>
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <table className="w-full text-left text-xs text-slate-300">
                <thead className="bg-slate-800/80 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
                  <tr>
                    <th className="p-3">Candidate ID</th>
                    <th className="p-3">Buyer / Party</th>
                    <th className="p-3">Total</th>
                    <th className="p-3">Paid / Due</th>
                    <th className="p-3">Posting Status</th>
                    <th className="p-3">Validation</th>
                    <th className="p-3">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {candidates.map((c) => (
                    <tr key={c.candidate_id} className="hover:bg-slate-800/40">
                      <td className="p-3 font-mono text-[11px] text-slate-400">{c.candidate_id.substring(0, 16)}...</td>
                      <td className="p-3 font-semibold text-white">{c.extracted_snapshot?.buyer_name || 'N/A'}</td>
                      <td className="p-3 font-bold">₹{c.extracted_snapshot?.total_amount || 0}</td>
                      <td className="p-3 text-[11px] text-slate-400">
                        ₹{c.extracted_snapshot?.amount_paid || 0} / ₹{c.extracted_snapshot?.credit_amount || 0}
                      </td>
                      <td className="p-3">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                          c.posting_status === 'POSTED' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' :
                          c.posting_status === 'NEEDS_REVIEW' ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' :
                          c.posting_status === 'CANCELLED' ? 'bg-slate-500/10 text-slate-400 border-slate-500/20' :
                          'bg-sky-500/10 text-sky-400 border-sky-500/20'
                        }`}>
                          {c.posting_status}
                        </span>
                      </td>
                      <td className="p-3 text-[11px]">{c.validation_status}</td>
                      <td className="p-3">
                        {c.posting_status === 'PENDING_CONFIRMATION' && (
                          <button
                            onClick={() => handleConfirmCandidate(c.candidate_id)}
                            className="px-2 py-1 text-[11px] bg-emerald-500 text-slate-950 font-bold rounded"
                          >
                            Commit
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab: Committed Transactions */}
        {activeTab === 'transactions' && (
          <div className="space-y-4">
            <h2 className="text-base font-bold text-white">Committed Financial Ledger Entries</h2>
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
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
                  {transactions.map((t) => (
                    <tr key={t.txn_id} className="hover:bg-slate-800/40">
                      <td className="p-3 font-mono text-[11px] text-slate-400">{t.txn_id}</td>
                      <td className="p-3">{t.business_date || 'Today'}</td>
                      <td className="p-3 font-semibold text-white">{t.intent}</td>
                      <td className="p-3">{t.party_id}</td>
                      <td className="p-3 font-bold text-emerald-400">₹{(t.total_minor / 100).toFixed(2)}</td>
                      <td className="p-3 text-amber-400">₹{(t.credit_minor / 100).toFixed(2)}</td>
                      <td className="p-3">
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          {t.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab: Inventory Detail */}
        {activeTab === 'inventory' && (
          <div className="space-y-4">
            <h2 className="text-base font-bold text-white">Master Inventory Catalog & Stock Movements</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {inventory.map((it) => (
                <div key={it.sku_id} className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2">
                  <div className="flex justify-between items-start">
                    <div>
                      <h3 className="font-bold text-white text-sm">{it.item_name}</h3>
                      <p className="text-xs text-slate-400">SKU: {it.sku_id}</p>
                    </div>
                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${it.below_reorder_level ? 'bg-rose-500/10 text-rose-400' : 'bg-emerald-500/10 text-emerald-400'}`}>
                      {it.below_reorder_level ? 'Low Stock' : 'In Stock'}
                    </span>
                  </div>
                  <div className="pt-2 border-t border-slate-800 flex justify-between items-baseline">
                    <span className="text-xs text-slate-400">Available:</span>
                    <span className="text-xl font-bold text-white">{it.current_stock} {it.unit}</span>
                  </div>
                  <div className="flex justify-between text-xs text-slate-400">
                    <span>Default Price:</span>
                    <span>₹{it.default_unit_price}</span>
                  </div>
                  <div className="flex justify-between text-xs text-slate-400">
                    <span>Reorder Level:</span>
                    <span>{it.reorder_level} {it.unit}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab: Live Extractor Demo Panel */}
        {activeTab === 'demo' && (
          <div className="max-w-3xl mx-auto space-y-6">
            <div className="border border-slate-800 rounded-xl p-6 bg-slate-900 space-y-4">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <FileText className="w-5 h-5 text-emerald-400" />
                  Live Multimodal Transaction Extractor
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  Submit vernacular voice transcript, invoice text, or WhatsApp messages directly to the Schema 1.1 extraction engine and Stage 4 validator.
                </p>
              </div>

              <form onSubmit={handleDemoExtract} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">
                    Merchant Raw Input (Telugu, Hindi, English, Hinglish):
                  </label>
                  <textarea
                    rows={4}
                    value={demoText}
                    onChange={(e) => setDemoText(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 focus:outline-none focus:border-emerald-500"
                    placeholder="e.g. Ramesh ki 50 shirts 200 each ki ammamu. 5000 cash, 5000 udhar."
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2 text-xs text-slate-400">
                    <span className="bg-slate-800 px-2 py-1 rounded">Model: Gemini 3.5/3.8 Flash</span>
                    <span className="bg-slate-800 px-2 py-1 rounded">Decimal Arithmetic</span>
                  </div>
                  <button
                    type="submit"
                    disabled={demoLoading}
                    className="px-5 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs transition flex items-center gap-2"
                  >
                    {demoLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : 'Run Extraction & Validation'}
                  </button>
                </div>
              </form>

              {/* Extractor Result Display */}
              {demoResult && (
                <div className="mt-4 pt-4 border-t border-slate-800 space-y-3">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                    Extraction & Validation Response
                  </h3>
                  <pre className="p-4 bg-slate-950 rounded-lg border border-slate-800 text-[11px] text-emerald-400 overflow-x-auto font-mono max-h-96">
                    {JSON.stringify(demoResult, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 bg-slate-900/50 py-3 px-6 text-center text-xs text-slate-500">
        Clandexis Invisible ERP • Startup Buildathon India 2026 • Google Cloud Project: <code>clandexis</code> (asia-south1)
      </footer>
    </div>
  );
}
