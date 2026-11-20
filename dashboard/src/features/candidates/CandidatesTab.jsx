import React from 'react';
import Badge from '../../components/common/Badge';

export default function CandidatesTab({ candidates = [], onConfirmCandidate }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-white">Full Transaction Candidate Staging Queue</h2>
        <span className="text-xs text-slate-400">Total: {candidates.length} candidates</span>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
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
              {candidates.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-400">
                    No transaction candidates found in the queue.
                  </td>
                </tr>
              ) : (
                candidates.map((c) => (
                  <tr key={c.candidate_id} className="hover:bg-slate-800/40 transition">
                    <td className="p-3 font-mono text-[11px] text-slate-400">{c.candidate_id}</td>
                    <td className="p-3 font-semibold text-white">
                      {c.extracted_snapshot?.buyer_name || c.extracted_snapshot?.party_name || 'N/A'}
                    </td>
                    <td className="p-3 font-bold">
                      ₹{c.extracted_snapshot?.total_amount != null ? Number(c.extracted_snapshot.total_amount).toFixed(2) : '0.00'}
                    </td>
                    <td className="p-3 text-[11px] text-slate-400">
                      ₹{c.extracted_snapshot?.amount_paid || 0} / ₹{c.extracted_snapshot?.credit_amount || 0}
                    </td>
                    <td className="p-3">
                      <Badge
                        variant={
                          c.posting_status === 'POSTED'
                            ? 'emerald'
                            : c.posting_status === 'NEEDS_REVIEW'
                            ? 'amber'
                            : c.posting_status === 'CANCELLED'
                            ? 'slate'
                            : 'sky'
                        }
                      >
                        {c.posting_status}
                      </Badge>
                    </td>
                    <td className="p-3 text-[11px]">
                      <span className="font-mono">{c.validation_status}</span>
                    </td>
                    <td className="p-3">
                      {c.posting_status === 'PENDING_CONFIRMATION' && (
                        <button
                          onClick={() => onConfirmCandidate(c.candidate_id)}
                          className="px-2.5 py-1 text-[11px] bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-lg transition shadow-sm"
                        >
                          Commit
                        </button>
                      )}
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
