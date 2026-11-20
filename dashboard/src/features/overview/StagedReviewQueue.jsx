import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
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
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          Pending Confirmations & Review Queue ({pendingCandidates.length})
        </h2>
        <span className="text-xs text-slate-400">Requires explicit merchant confirmation</span>
      </div>

      <div className="space-y-3">
        {pendingCandidates.length === 0 ? (
          <div className="p-8 text-center bg-slate-900/40 border border-slate-800 rounded-xl text-slate-400 text-xs">
            No pending reviews or unconfirmed transactions. All candidates committed or clear!
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
  const [reviewNote, setReviewNote] = useState('');
  const [reviewJson, setReviewJson] = useState(
    JSON.stringify(cand.extracted_snapshot, null, 2)
  );
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

  const handleReviewSubmit = async (e) => {
    e.preventDefault();
    setReviewing(true);
    try {
      const parsed = JSON.parse(reviewJson);
      await onReviewCandidate(cand.candidate_id, parsed, reviewNote);
    } finally {
      setReviewing(false);
    }
  };

  return (
    <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3 relative hover:border-slate-700 transition">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-white">
              {cand.extracted_snapshot?.buyer_name || cand.extracted_snapshot?.party_name || 'Customer'}
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
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Source Msg: <code className="text-slate-300 font-mono text-[11px]">{cand.source_message_id}</code>
          </p>
        </div>
        <div className="text-right">
          <span className="text-base font-bold text-white">
            {cand.extracted_snapshot?.total_amount == null
              ? 'Total unknown'
              : `₹${Number(cand.extracted_snapshot.total_amount).toFixed(2)}`}
          </span>
          <p className="text-[11px] text-slate-400">
            Paid: {cand.extracted_snapshot?.amount_paid ?? 'unknown'} • Due: {cand.extracted_snapshot?.credit_amount ?? 'unknown'}
          </p>
        </div>
      </div>

      {/* Line items summary */}
      {cand.extracted_snapshot?.items && cand.extracted_snapshot.items.length > 0 && (
        <div className="bg-slate-950/60 rounded-lg p-2.5 text-xs text-slate-300 border border-slate-800/80">
          <div className="font-medium text-slate-400 text-[11px] mb-1">Items:</div>
          {cand.extracted_snapshot.items.map((it, idx) => (
            <div key={idx} className="flex justify-between text-xs py-0.5">
              <span>{it.quantity} {it.item_name} @ ₹{it.unit_price}</span>
              <span>₹{it.line_total}</span>
            </div>
          ))}
        </div>
      )}

      {/* Warnings / Clarifications */}
      {cand.validation_warnings?.length > 0 && (
        <div className="text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/20 p-2 rounded-lg">
          ⚠️ {cand.validation_warnings.join(', ')}
        </div>
      )}

      {cand.extracted_snapshot?.clarification_question && (
        <div className="text-[11px] text-sky-300 bg-sky-500/10 border border-sky-500/20 p-2 rounded-lg">
          💬 {cand.extracted_snapshot.clarification_question}
        </div>
      )}

      {/* Clarification Reply Form */}
      {cand.posting_status === 'NEEDS_REVIEW' && (
        <form onSubmit={handleClarifySubmit} className="pt-2 border-t border-slate-800 flex items-center gap-2">
          <label className="text-xs text-slate-400 flex-1 flex items-center gap-2">
            <span className="whitespace-nowrap">Reply to clarify:</span>
            <input
              type="text"
              value={clarifyAnswer}
              onChange={(e) => setClarifyAnswer(e.target.value)}
              placeholder="Provide missing details..."
              required
              maxLength={1000}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:border-sky-500"
            />
          </label>
          <button
            type="submit"
            disabled={clarifying}
            className="px-3 py-1 bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 border border-sky-500/20 rounded-lg text-xs font-semibold transition"
          >
            {clarifying ? 'Sending...' : 'Send reply'}
          </button>
        </form>
      )}

      {/* JSON Review Form */}
      {(cand.posting_status === 'NEEDS_REVIEW' || cand.validation_status === 'NEEDS_REVIEW') && (
        <form onSubmit={handleReviewSubmit} className="pt-2 border-t border-slate-800 space-y-2">
          <label className="block text-xs text-slate-400">
            <span className="font-medium text-slate-300 mb-1 block">Review extracted facts (JSON):</span>
            <textarea
              rows={4}
              value={reviewJson}
              onChange={(e) => setReviewJson(e.target.value)}
              className="block w-full bg-slate-950 font-mono text-[11px] text-emerald-400 border border-slate-800 rounded-lg p-2 focus:outline-none focus:border-emerald-500"
            />
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              required
              placeholder="What did you verify or correct?"
              className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:border-emerald-500"
            />
            <button
              type="submit"
              disabled={reviewing}
              className="px-3 py-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 rounded-lg text-xs font-semibold transition"
            >
              {reviewing ? 'Saving...' : 'Save review'}
            </button>
          </div>
        </form>
      )}

      {/* Safety Gate Decision Actions */}
      <div className="flex items-center justify-end space-x-2 pt-1 border-t border-slate-800">
        <button
          onClick={() => onCancelCandidate(cand.candidate_id)}
          className="px-3 py-1 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
        >
          Reject / Cancel
        </button>
        <button
          disabled={cand.posting_status !== 'PENDING_CONFIRMATION'}
          onClick={() => onConfirmCandidate(cand.candidate_id)}
          className="px-3 py-1 text-xs font-bold rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-sm transition disabled:opacity-40 disabled:hover:bg-emerald-500"
        >
          ✅ Authorize & Commit to Ledger
        </button>
      </div>
    </div>
  );
}
