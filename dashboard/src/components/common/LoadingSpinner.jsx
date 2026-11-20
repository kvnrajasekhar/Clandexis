import React from 'react';
import { RefreshCw } from 'lucide-react';

export default function LoadingSpinner({ size = 'md', text = 'Loading...' }) {
  const iconSizes = {
    sm: 'w-3.5 h-3.5',
    md: 'w-5 h-5',
    lg: 'w-8 h-8',
  };

  return (
    <div className="flex flex-col items-center justify-center p-8 space-y-3 text-slate-400">
      <RefreshCw className={`${iconSizes[size] || iconSizes.md} animate-spin text-emerald-400`} />
      {text && <span className="text-xs font-medium">{text}</span>}
    </div>
  );
}
