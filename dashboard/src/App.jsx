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



export default function App() {
  const [session, setSession] = useState(null);
  const [loginError, setLoginError] = useState('');
  const [googleClientId, setGoogleClientId] = useState('');
  const MERCHANT_ID = session?.merchant_id || '';
  const apiFetch = (url, options = {}) => fetch(url, {
    ...options, credentials: 'same-origin',
    headers: { ...options.headers, 'X-CSRF-Token': session?.csrf_token || '' },
  }).then(response => {
    if (response.status === 401) setSession(null);
    return response;
  });

  useEffect(() => {
    fetch('/api/v1/auth/session', { credentials: 'same-origin' })
      .then(response => response.ok ? response.json() : null)
      .then(setSession);
    fetch('/api/v1/auth/config')
      .then(res => res.ok ? res.json() : null)
      .then(cfg => {
        if (cfg?.google_client_id) setGoogleClientId(cfg.google_client_id);
      })
      .catch(console.error);
  }, []);

  const handleGoogleLogin = async (credential) => {
    try {
      const res = await fetch('/api/v1/auth/google', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential })
      });
      if (res.ok) {
        setSession(await res.json());
        setLoginError('');
      } else {
        const err = await res.json();
        setLoginError(err.detail || 'Google sign-in failed');
      }
    } catch (e) {
      setLoginError(e.message || 'Network error during Google sign-in');
    }
  };

  useEffect(() => {
    if (session || !googleClientId) return;
    const interval = setInterval(() => {
      if (window.google?.accounts?.id) {
        clearInterval(interval);
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: (resp) => handleGoogleLogin(resp.credential)
        });
        const container = document.getElementById('google-signin-btn');
        if (container) {
          container.innerHTML = '';
          window.google.accounts.id.renderButton(container, {
            theme: 'filled_black',
            size: 'large',
            shape: 'pill',
            width: 280,
            text: 'continue_with'
          });
        }
      }
    }, 200);
    return () => clearInterval(interval);
  }, [session, googleClientId]);

  const logout = async () => {
    try {
      await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' });
    } catch (_) {}
    setSession(null);
  };

  const loginWithDemo = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.get('username') || 'demo', password: form.get('password') || '' }),
    });
    if (response.ok) { setSession(await response.json()); setLoginError(''); }
    else setLoginError('Sign-in failed. Please verify credentials or use Google.');
  };
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
  const [demoText, setDemoText] = useState("Sold 50 Cotton Shirt (L) to Suresh Traders at INR 200 each, total INR 10000. Received INR 2000 by UPI. Remaining INR 8000 on credit.");
  const [audioFile, setAudioFile] = useState(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoResult, setDemoResult] = useState(null);

  const fetchData = async () => {
    try {
      if (!session) return;
      setRefreshing(true);
      const [sumRes, invRes, partRes, txnRes, candRes] = await Promise.all([
        apiFetch(`/api/v1/dashboard/summary?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        apiFetch(`/api/v1/dashboard/inventory?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        apiFetch(`/api/v1/dashboard/parties?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        apiFetch(`/api/v1/dashboard/transactions?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
        apiFetch(`/api/v1/dashboard/candidates?merchant_id=${encodeURIComponent(MERCHANT_ID)}`),
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
    if (!session) return;
    fetchData();
    const interval = setInterval(fetchData, 8000);
    return () => clearInterval(interval);
  }, [session]);

  const handleConfirmCandidate = async (candidateId) => {
    try {
      const res = await apiFetch('/api/v1/dashboard/candidates/confirm', {
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
      const res = await apiFetch('/api/v1/dashboard/candidates/cancel', {
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
      const fields = { merchant_id: MERCHANT_ID, message_id: crypto.randomUUID(),
        merchant_business_name: "Sri Balaji Textiles", stage: true };
      let response;
      if (audioFile) {
        const form = new FormData();
        Object.entries(fields).forEach(([key, value]) => form.append(key, value));
        form.append('file', audioFile);
        response = await apiFetch('/api/v1/extract/media', {method: 'POST', body: form});
      } else {
        response = await apiFetch('/api/v1/extract/text', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...fields, raw_text: demoText})});
      }
      const data = await response.json();
      setDemoResult(data);
      fetchData();
    } catch (err) {
      setDemoResult({ error: err.message });
    } finally {
      setDemoLoading(false);
    }
  };

  if (!session) return (
    <main className="min-h-screen bg-slate-950 text-slate-100 grid place-items-center p-4">
      <div className="w-full max-w-sm p-8 bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl space-y-6 text-center">
        <div className="mx-auto h-12 w-12 rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 text-2xl shadow-lg shadow-emerald-500/20">
          ⚡
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">Clandexis Invisible ERP</h1>
          <p className="text-xs text-slate-400 mt-1">Sign in with Google to access your enterprise dashboard</p>
        </div>

        <div className="pt-2 flex flex-col items-center space-y-3 w-full">
          {googleClientId ? (
            <div id="google-signin-btn" className="min-h-[44px] flex justify-center items-center">
              <div className="animate-pulse text-xs text-slate-500">Loading Google Sign-In...</div>
            </div>
          ) : (
            <button
              onClick={() => handleGoogleLogin('test:indra.gottipati7@gmail.com')}
              className="w-full flex items-center justify-center gap-3 bg-white hover:bg-slate-100 text-slate-900 font-semibold py-2.5 px-4 rounded-xl shadow transition text-xs"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
              </svg>
              Sign in with Google
            </button>
          )}
          {loginError && <p role="alert" className="text-xs text-rose-400 font-medium">{loginError}</p>}
        </div>

        <div className="pt-4 border-t border-slate-800/80 text-[11px] text-slate-500">
          Secure, authenticated merchant session • Multi-tenant isolation
        </div>
      </div>
    </main>
  );

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
                Production Dashboard
              </span>
            </div>
            <p className="text-xs text-slate-400 flex items-center gap-1.5 mt-0.5">
              <Building2 className="w-3.5 h-3.5 text-slate-500" />
              <span className="text-slate-200 font-semibold">{session?.business_name || 'My Enterprise'}</span>
              {session?.name && <span>• {session.name}</span>}
              {session?.email && <span className="text-slate-500">({session.email})</span>}
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
                {tab === 'demo' ? '⚡ Extractor' : tab}
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

          <button
            onClick={logout}
            className="px-3 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg border border-rose-500/20 transition text-xs font-semibold"
            title="Sign out"
          >
            Sign out
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
                    ₹{summary ? summary.sales_total.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
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
                    ₹{summary ? summary.credit_extended.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Collections: ₹{summary ? summary.collections_total : 0}
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
                    ₹{summary ? summary.total_receivables_outstanding.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}
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
                                {cand.extracted_snapshot?.total_amount == null ? "Total unknown" : `₹${cand.extracted_snapshot.total_amount.toFixed(2)}`}
                              </span>
                              <p className="text-[11px] text-slate-400">
                                Paid: {cand.extracted_snapshot?.amount_paid ?? "unknown"} • Due: {cand.extracted_snapshot?.credit_amount ?? "unknown"}
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
                              💬 {cand.extracted_snapshot.clarification_question}
                            </div>
                          )}

                          {cand.posting_status === 'NEEDS_REVIEW' && (
                            <form onSubmit={async event => {
                              event.preventDefault();
                              const form = event.currentTarget;
                              const answer = new FormData(form).get('answer').trim();
                              if (!answer) return;
                              try {
                                const res = await apiFetch(`/api/v1/dashboard/candidates/${cand.candidate_id}/clarify`, {
                                  method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify({answer, revision: cand.revision})
                                });
                                const data = await res.json();
                                if (res.ok) form.reset();
                                setActionMessage({type: res.ok ? 'info' : 'error', text: res.ok
                                  ? (data.question || 'Reply saved. Review the updated details before confirming.')
                                  : (data.detail || 'Unable to save reply.')});
                                await fetchData();
                              } catch { setActionMessage({type:'error', text:'Unable to send reply. Refresh and try again.'}); }
                            }}>
                              <label>Reply to clarify <input className="bg-slate-800" name="answer" required maxLength={1000} /></label>
                              <button className="ml-2 underline">Send reply</button>
                            </form>
                          )}
                          {(cand.posting_status === 'NEEDS_REVIEW' || cand.validation_status === 'NEEDS_REVIEW') && (
                            <form key={cand.candidate_id} onSubmit={async e => {
                              e.preventDefault();
                              const values = new FormData(e.currentTarget);
                              try {
                                const response = await apiFetch(`/api/v1/dashboard/candidates/${cand.candidate_id}/review`, {
                                  method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify({candidate: JSON.parse(values.get('facts')), note: values.get('note')})
                                });
                                const body = await response.json();
                                setActionMessage({type: response.ok ? 'info' : 'error', text: body.review_resolved ? 'Review saved. Check details and confirm.' : body.detail || 'Still requires review.'});
                                fetchData();
                              } catch { setActionMessage({type:'error', text:'Enter valid transaction JSON.'}); }
                            }}>
                              <label>Review extracted facts<textarea className="block w-full bg-slate-800" name="facts" defaultValue={JSON.stringify(cand.extracted_snapshot, null, 2)} /></label>
                              <input className="bg-slate-800" name="note" required placeholder="What did you verify or correct?" />
                              <button>Save review</button>
                            </form>
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
                              disabled={cand.posting_status !== "PENDING_CONFIRMATION"}
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
                      <td className="p-3 font-mono text-[11px] text-slate-400">{c.candidate_id}</td>
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
                      <td className="p-3 font-mono text-[11px] text-slate-400">{t.txn_id}
                        {t.intent === 'SALE_INVOICE' && <a className="block underline text-sky-300" href={`/api/v1/dashboard/transactions/${encodeURIComponent(t.txn_id)}/invoice`}>Download invoice</a>}
                      </td>
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
                <label>Upload audio (takes precedence over text)
                  <input type="file" accept="audio/*" onChange={e => setAudioFile(e.target.files[0])} />
                </label>
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
                    <span className="bg-slate-800 px-2 py-1 rounded">Gemini</span>
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
                  {demoResult.staged_candidates?.map(row => (
                    <div key={row.candidate_id} className="mb-2 text-sm">
                      <code>{row.candidate_id}</code> · {row.validation_status} · {row.posting_status}
                      {row.posting_status === 'NEEDS_REVIEW' && <p>{demoResult.extraction.transactions[row.candidate_index]?.clarification_question} <button className="underline" onClick={() => setActiveTab('overview')}>Answer in Overview</button></p>}
                      <button className="ml-3 underline text-sky-300" onClick={() => setActiveTab('candidates')}>View candidates</button>
                    </div>
                  ))}
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
