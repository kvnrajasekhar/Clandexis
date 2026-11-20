import React from 'react';

export default function AlertBanner({ actionMessage, onClose }) {
  if (!actionMessage) return null;

  const type = actionMessage.type || 'info';
  const colorClasses =
    type === 'error'
      ? 'bg-rose-500/10 border-rose-500/20 text-rose-300'
      : type === 'info'
      ? 'bg-sky-500/10 border-sky-500/20 text-sky-300'
      : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300';

  return (
    <div className={`px-6 py-2.5 text-xs font-medium flex items-center justify-between border-b ${colorClasses}`}>
      <span>{actionMessage.text}</span>
      {onClose && (
        <button
          onClick={onClose}
          className="opacity-70 hover:opacity-100 font-bold px-1.5 py-0.5 rounded hover:bg-slate-800/30 transition"
          aria-label="Close notification"
        >
          ✕
        </button>
      )}
    </div>
  );
}
