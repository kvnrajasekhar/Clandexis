import React from 'react';
import BusinessPulseCards from './BusinessPulseCards';
import StagedReviewQueue from './StagedReviewQueue';
import CounterpartyUdhaar from './CounterpartyUdhaar';
import InventoryLevels from './InventoryLevels';

export default function OverviewTab({
  summary,
  parties = [],
  inventory = [],
  candidates = [],
  onConfirmCandidate,
  onCancelCandidate,
  onClarifyCandidate,
  onReviewCandidate,
}) {
  return (
    <div className="space-y-6">
      {/* Daily Business Pulse (KPI Cards) */}
      <BusinessPulseCards summary={summary} partyCount={parties.length} />

      {/* Two-Column Layout: Staged Candidates & Parties / Inventory */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Staged Action Panel (Human-in-the-Loop Safety Gate) */}
        <div className="lg:col-span-2">
          <StagedReviewQueue
            candidates={candidates}
            onConfirmCandidate={onConfirmCandidate}
            onCancelCandidate={onCancelCandidate}
            onClarifyCandidate={onClarifyCandidate}
            onReviewCandidate={onReviewCandidate}
          />
        </div>

        {/* Right Col: Counterparties & Credit Limits + Inventory */}
        <div className="space-y-4">
          <CounterpartyUdhaar parties={parties} />
          <InventoryLevels inventory={inventory} />
        </div>
      </div>
    </div>
  );
}
