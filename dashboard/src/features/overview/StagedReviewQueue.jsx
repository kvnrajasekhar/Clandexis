import React, { useState } from 'react';
import { AlertTriangle, CheckCircle2, User, Package, Wallet, CreditCard, Edit3, MessageSquare } from 'lucide-react';
import Badge from '../../components/common/Badge';

export default function StagedReviewQueue({
  candidates = [],
  onConfirmCandidate,
  onCancelCandidate,
  onClarifyCandidate,
  onReviewCandidate,
}) {
  const pendingCandidates = candidates.filter(
    (c) => c.posting_status !== 'POSTED' && c.posting_status !== 'CANCELLED'
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>Pending Confirmations & Review Queue ({pendingCandidates.length})</span>
        </h2>
        <span className="text-xs text-slate-400">Requires merchant authorization</span>
      </div>

      <div className="space-y-3">
        {pendingCandidates.length === 0 ? (
          <div className="p-8 text-center bg-slate-900/40 border border-slate-800 rounded-xl text-slate-400 text-xs">
            <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto mb-2 opacity-60" />
            No pending reviews or unconfirmed transactions. All transactions committed or clear!
          </div>
        ) : (
          pendingCandidates.map((cand) => (
            <CandidateCard
              key={cand.candidate_id}
              cand={cand}
              onConfirmCandidate={onConfirmCandidate}
              onCancelCandidate={onCancelCandidate}
              onClarifyCandidate={onClarifyCandidate}
              onReviewCandidate={onReviewCandidate}
            />
          ))
        )}
      </div>
    </div>
  );
}

function CandidateCard({
  cand,
  onConfirmCandidate,
  onCancelCandidate,
  onClarifyCandidate,
  onReviewCandidate,
}) {
  const [clarifyAnswer, setClarifyAnswer] = useState('');
  const [clarifying, setClarifying] = useState(false);

  // Review card editing state
  const snapshot = cand.extracted_snapshot || {};
  const [isEditingReview, setIsEditingReview] = useState(false);
  const [buyerName, setBuyerName] = useState(snapshot.buyer_name || snapshot.party_name || '');
  const [totalAmount, setTotalAmount] = useState(snapshot.total_amount ?? '');
  const [amountPaid, setAmountPaid] = useState(snapshot.amount_paid ?? '');
  const [creditAmount, setCreditAmount] = useState(snapshot.credit_amount ?? '');
  const [reviewNote, setReviewNote] = useState('');
  const [reviewing, setReviewing] = useState(false);

  const handleClarifySubmit = async (e) => {
    e.preventDefault();
    if (!clarifyAnswer.trim()) return;
    setClarifying(true);
    try {
      await onClarifyCandidate(cand.candidate_id, clarifyAnswer.trim(), cand.revision);
      setClarifyAnswer('');
    } finally {
      setClarifying(false);
    }
  };

  const handleReviewCardSubmit = async (e) => {
    e.preventDefault();
    setReviewing(true);
    try {
      // Build clean JSON snapshot without asking user to write JSON
      const updatedSnapshot = {
        ...snapshot,
        buyer_name: buyerName.trim(),
        party_name: buyerName.trim(),
        total_amount: totalAmount !== '' ? Number(totalAmount) : snapshot.total_amount,
        amount_paid: amountPaid !== '' ? Number(amountPaid) : snapshot.amount_paid,
        credit_amount: creditAmount !== '' ? Number(creditAmount) : snapshot.credit_amount,
      };
      await onReviewCandidate(cand.candidate_id, updatedSnapshot, reviewNote.trim() || 'Verified by merchant in dashboard');
      setIsEditingReview(false);
    } finally {
      setReviewing(false);
    }
  };

  const needsReview = cand.posting_status === 'NEEDS_REVIEW' || cand.validation_status === 'NEEDS_REVIEW';

  return (
    <div className="p-3.5 sm:p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3 relative hover:border-slate-700 transition">
      {/* Header Info */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-white truncate">
              {snapshot.buyer_name || snapshot.party_name || 'Customer'}
            </span>
            <Badge
              variant={
                cand.posting_status === 'NEEDS_REVIEW'
                  ? 'amber'
                  : cand.posting_status === 'POSTED'
                  ? 'emerald'
                  : 'sky'
              }
            >
              {cand.posting_status}
            </Badge>
            {cand.validation_status && (
              <span className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded font-mono">
                {cand.validation_status}
              </span>
            )}
          </div>
          <p className="text-[11px] text-slate-400 mt-0.5 break-all">
            Source Msg: <code className="text-slate-300 font-mono">{cand.source_message_id}</code>
          </p>
        </div>

        {/* Financial Highlights Card */}
        <div className="sm:text-right bg-slate-950/60 p-2 sm:p-0 rounded-lg sm:bg-transparent">
          <span className="text-base font-bold text-white block">
            {snapshot.total_amount == null
              ? 'Total unknown'
              : `₹${Number(snapshot.total_amount).toFixed(2)}`}
          </span>
          <div className="text-[11px] text-slate-400 flex sm:justify-end items-center gap-2 mt-0.5">
            <span className="text-emerald-400 font-medium">Paid: ₹{snapshot.amount_paid ?? 0}</span>
            <span>•</span>
            <span className="text-amber-400 font-medium">Due: ₹{snapshot.credit_amount ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Structured Line Items Table Card */}
      {snapshot.items && snapshot.items.length > 0 && (
        <div className="bg-slate-950/70 rounded-lg p-2.5 text-xs text-slate-300 border border-slate-800/80">
          <div className="font-medium text-slate-400 text-[11px] mb-1.5 flex items-center gap-1.5">
            <Package className="w-3.5 h-3.5 text-teal-400" />
            <span>Items Breakdown ({snapshot.items.length}):</span>
          </div>
          <div className="space-y-1 divide-y divide-slate-800/50">
            {snapshot.items.map((it, idx) => (
              <div key={idx} className="flex justify-between items-center text-xs pt-1 first:pt-0">
                <span className="text-slate-200">
                  <span className="font-semibold text-white">{it.quantity}</span> × {it.item_name}
                  {it.unit_price ? <span className="text-slate-400 text-[11px] ml-1">(@ ₹{it.unit_price})</span> : null}
                </span>
                <span className="font-mono text-emerald-400 font-medium">₹{it.line_total || (Number(it.quantity) * Number(it.unit_price || 0))}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Warnings Callout */}
      {cand.validation_warnings?.length > 0 && (
        <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 p-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-[11px] uppercase tracking-wide text-amber-400 mb-0.5">Validation Notice</div>
            <div>{cand.validation_warnings.join(', ')}</div>
          </div>
        </div>
      )}

      {/* Clarification Question Prompt */}
      {snapshot.clarification_question && (
        <div className="text-xs text-sky-300 bg-sky-500/10 border border-sky-500/20 p-2.5 rounded-lg flex items-start gap-2">
          <MessageSquare className="w-4 h-4 text-sky-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-[11px] uppercase tracking-wide text-sky-400 mb-0.5">Clarification Required</div>
            <div>{snapshot.clarification_question}</div>
          </div>
        </div>
      )}

      {/* Clarification Reply Form (Plain conversational response) */}
      {cand.posting_status === 'NEEDS_REVIEW' && (
        <form onSubmit={handleClarifySubmit} className="pt-2 border-t border-slate-800 flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <input
            type="text"
            value={clarifyAnswer}
            onChange={(e) => setClarifyAnswer(e.target.value)}
            placeholder="Reply with missing price, quantity, or buyer name..."
            required
            maxLength={1000}
            className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-sky-500"
          />
          <button
            type="submit"
            disabled={clarifying}
            className="px-3.5 py-1.5 bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 border border-sky-500/20 rounded-lg text-xs font-semibold transition shrink-0 text-center"
          >
            {clarifying ? 'Sending...' : 'Send Clarification'}
          </button>
        </form>
      )}

      {/* Human-in-the-Loop Review Form: Card Format (NO RAW JSON TEXTAREA) */}
      {needsReview && (
        <div className="pt-2 border-t border-slate-800 space-y-2">
          {!isEditingReview ? (
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 p-2.5 rounded-lg bg-amber-500/5 border border-amber-500/20">
              <div className="text-xs text-amber-300">
                <span className="font-semibold">Review Required:</span> Check buyer and financial values before committing.
              </div>
              <button
                type="button"
                onClick={() => setIsEditingReview(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded-lg text-xs font-semibold transition"
              >
                <Edit3 className="w-3.5 h-3.5" />
                Review & Edit Details
              </button>
            </div>
          ) : (
            <form onSubmit={handleReviewCardSubmit} className="p-3 bg-slate-950/80 rounded-xl border border-slate-800 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-white flex items-center gap-1.5">
                  <Edit3 className="w-3.5 h-3.5 text-emerald-400" />
                  Edit Transaction Card Details
                </span>
                <button
                  type="button"
                  onClick={() => setIsEditingReview(false)}
                  className="text-slate-400 hover:text-slate-200 text-xs"
                >
                  Cancel
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1">Buyer / Party Name</label>
                  <input
                    type="text"
                    value={buyerName}
                    onChange={(e) => setBuyerName(e.target.value)}
                    required
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1">Total Amount (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={totalAmount}
                    onChange={(e) => setTotalAmount(e.target.value)}
                    required
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1">Amount Paid (Cash/UPI ₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={amountPaid}
                    onChange={(e) => setAmountPaid(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1">Credit / Udhaar Due (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={creditAmount}
                    onChange={(e) => setCreditAmount(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-slate-300 mb-1">Verification Note</label>
                <input
                  type="text"
                  value={reviewNote}
                  onChange={(e) => setReviewNote(e.target.value)}
                  placeholder="e.g., Verified prices with buyer over WhatsApp"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setIsEditingReview(false)}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs"
                >
                  Discard
                </button>
                <button
                  type="submit"
                  disabled={reviewing}
                  className="px-4 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-lg text-xs transition disabled:opacity-50"
                >
                  {reviewing ? 'Saving Card...' : 'Save & Verify Details'}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Safety Gate Decision Actions (Responsive) */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button
          onClick={() => onCancelCandidate(cand.candidate_id)}
          className="w-full sm:w-auto px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition text-center"
        >
          Reject / Cancel
        </button>
        <button
          disabled={cand.posting_status !== 'PENDING_CONFIRMATION'}
          onClick={() => onConfirmCandidate(cand.candidate_id)}
          className="w-full sm:w-auto px-4 py-1.5 text-xs font-bold rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-sm transition disabled:opacity-40 disabled:cursor-not-allowed text-center"
        >
          ✅ Authorize & Commit to Ledger
        </button>
      </div>
    </div>
  );
}
