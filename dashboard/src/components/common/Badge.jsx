import React from 'react';

export default function Badge({ children, variant = 'sky', className = '' }) {
  const styles = {
    emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    amber: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
    rose: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
    sky: 'bg-sky-500/10 text-sky-400 border-sky-500/30',
    slate: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
  };

  const selectedStyle = styles[variant] || styles.sky;

  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border tracking-wider uppercase inline-flex items-center gap-1 ${selectedStyle} ${className}`}>
      {children}
    </span>
  );
}
