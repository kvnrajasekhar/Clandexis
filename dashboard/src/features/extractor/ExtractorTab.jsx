import React, { useState } from 'react';
import { FileText, RefreshCw, Upload, CheckCircle2, AlertTriangle, Package, ChevronDown, ChevronUp, ArrowRight } from 'lucide-react';
import { extractTransaction } from '../../api/dashboardApi';
import Badge from '../../components/common/Badge';

export default function ExtractorTab({
  session,
  merchantId,
  onExtractionSuccess,
  onNavigateTab,
}) {
  const [demoText, setDemoText] = useState('');
  const [audioFile, setAudioFile] = useState(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoResult, setDemoResult] = useState(null);
  const [showRawJson, setShowRawJson] = useState(false);

  const handleDemoExtract = async (e) => {
    e.preventDefault();
    setDemoLoading(true);
    setDemoResult(null);

    try {
      const data = await extractTransaction({
        merchantId,
        businessName: session?.business_name,
        audioFile,
        demoText,
      });
      setDemoResult(data);
      if (onExtractionSuccess) onExtractionSuccess();
    } catch (err) {
      setDemoResult({ error: err.message });
    } finally {
      setDemoLoading(false);
    }
  };

  const extractedTransactions = demoResult?.extraction?.transactions || [];

  return (
    <div className="max-w-3xl mx-auto space-y-4 sm:space-y-6">
      <div className="border border-slate-800 rounded-xl p-4 sm:p-6 bg-slate-900 space-y-4 shadow-sm">
        <div>
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <FileText className="w-5 h-5 text-emerald-400 shrink-0" />
            <span>Live Multimodal Transaction Extractor</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Submit vernacular voice audio, invoice text, or WhatsApp messages directly to the Schema 1.1 extraction engine and Stage 4 validator.
          </p>
        </div>

        <form onSubmit={handleDemoExtract} className="space-y-4">
          {/* Audio File Input */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center gap-1.5">
              <Upload className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
              Upload Audio (Voice note - takes precedence over text):
            </label>
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => setAudioFile(e.target.files ? e.target.files[0] : null)}
              className="block w-full text-xs text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-emerald-500/10 file:text-emerald-400 hover:file:bg-emerald-500/20 cursor-pointer"
            />
          </div>

          {/* Vernacular Raw Text Input */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Merchant Raw Input (Telugu, Hindi, English, Hinglish):
            </label>
            <textarea
              rows={4}
              value={demoText}
              onChange={(e) => setDemoText(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 focus:outline-none focus:border-emerald-500 transition"
              placeholder="e.g. Ramesh ki 50 shirts 200 each ki ammamu. 5000 cash, 5000 udhar."
            />
          </div>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-1">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <span className="bg-slate-800 px-2 py-1 rounded">Gemini Multimodal</span>
              <span className="bg-slate-800 px-2 py-1 rounded">Stage 4 Validator</span>
              <span className="bg-slate-800 px-2 py-1 rounded">Decimal Math</span>
            </div>
            <button
              type="submit"
              disabled={demoLoading}
              className="w-full sm:w-auto px-5 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs transition flex items-center justify-center gap-2 shadow-sm disabled:opacity-50"
            >
              {demoLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : 'Run Extraction & Validation'}
            </button>
          </div>
        </form>

        {/* Extractor Result Display: Card Formats */}
        {demoResult && (
          <div className="mt-4 pt-4 border-t border-slate-800 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                Extracted Transaction Cards
              </h3>
              {demoResult.staged_candidates && (
                <span className="text-xs text-slate-400">
                  {demoResult.staged_candidates.length} candidate(s) created
                </span>
              )}
            </div>

            {demoResult.error && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 rounded-xl text-xs">
                Extraction Error: {demoResult.error}
              </div>
            )}

            {/* Structured Transaction Cards */}
            {demoResult.staged_candidates && demoResult.staged_candidates.length > 0 ? (
              <div className="space-y-3">
                {demoResult.staged_candidates.map((cand, idx) => {
                  const txnData = extractedTransactions[cand.candidate_index ?? idx] || {};
                  return (
                    <div
                      key={cand.candidate_id || idx}
                      className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-3 relative hover:border-slate-700 transition"
                    >
                      {/* Top Bar of Card */}
                      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-bold text-white">
                              {txnData.party_name || txnData.buyer_name || 'Customer'}
                            </span>
                            <Badge
                              variant={
                                cand.posting_status === 'POSTED'
                                  ? 'emerald'
                                  : cand.posting_status === 'NEEDS_REVIEW'
                                  ? 'amber'
                                  : 'sky'
                              }
                            >
                              {cand.posting_status}
                            </Badge>
                            {cand.validation_status && (
                              <span className="text-[10px] text-slate-400 bg-slate-800 px-2 py-0.5 rounded font-mono">
                                {cand.validation_status}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            Intent: <span className="text-slate-200 font-medium">{txnData.intent || 'SALE'}</span> • Candidate ID: <code className="text-emerald-400">{cand.candidate_id}</code>
                          </div>
                        </div>

                        <div className="sm:text-right">
                          <div className="text-base font-bold text-white">
                            ₹{txnData.total_amount != null ? Number(txnData.total_amount).toFixed(2) : '0.00'}
                          </div>
                          <div className="text-[11px] text-slate-400">
                            Cash: ₹{txnData.cash_amount ?? txnData.amount_paid ?? 0} • Udhaar: ₹{txnData.credit_amount ?? 0}
                          </div>
                        </div>
                      </div>

                      {/* Items Details Card */}
                      {txnData.items && txnData.items.length > 0 && (
                        <div className="bg-slate-900/80 rounded-lg p-2.5 text-xs text-slate-300 border border-slate-800">
                          <div className="font-medium text-slate-400 text-[11px] mb-1 flex items-center gap-1">
                            <Package className="w-3.5 h-3.5 text-teal-400" />
                            <span>Extracted Products ({txnData.items.length}):</span>
                          </div>
                          <div className="divide-y divide-slate-800">
                            {txnData.items.map((item, itemIdx) => (
                              <div key={itemIdx} className="flex justify-between items-center py-1 text-xs">
                                <span>
                                  <strong className="text-white">{item.quantity}</strong> {item.unit || ''} {item.item_name}
                                  {item.unit_price ? <span className="text-slate-400 text-[11px] ml-1">@ ₹{item.unit_price}</span> : null}
                                </span>
                                <span className="font-mono text-emerald-400">₹{item.line_total}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Clarification prompt if any */}
                      {txnData.clarification_question && (
                        <div className="text-xs text-sky-300 bg-sky-500/10 border border-sky-500/20 p-2.5 rounded-lg flex items-start justify-between gap-2">
                          <div>
                            <span className="font-semibold block text-sky-400">Clarification Question:</span>
                            <span>{txnData.clarification_question}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => onNavigateTab && onNavigateTab('overview')}
                            className="shrink-0 px-2.5 py-1 bg-sky-500 text-slate-950 font-bold rounded text-[11px] transition"
                          >
                            Answer in Overview
                          </button>
                        </div>
                      )}

                      {/* Card Action Link */}
                      <div className="flex justify-end pt-1">
                        <button
                          type="button"
                          onClick={() => onNavigateTab && onNavigateTab('candidates')}
                          className="text-xs text-emerald-400 hover:text-emerald-300 font-semibold inline-flex items-center gap-1"
                        >
                          View in Candidates Queue <ArrowRight className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}

            {/* Collapsible Developer JSON view */}
            <div className="pt-2">
              <button
                type="button"
                onClick={() => setShowRawJson(!showRawJson)}
                className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1 transition"
              >
                {showRawJson ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                {showRawJson ? 'Hide Technical JSON' : 'Show Technical JSON (Developer Mode)'}
              </button>
              {showRawJson && (
                <pre className="mt-2 p-3 bg-slate-950 rounded-lg border border-slate-800 text-[11px] text-emerald-400 overflow-x-auto font-mono max-h-80">
                  {JSON.stringify(demoResult, null, 2)}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
