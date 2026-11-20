import React from 'react';
import { Building2, Phone, RefreshCw } from 'lucide-react';

export default function Header({
  session,
  activeTab,
  setActiveTab,
  refreshing,
  onRefresh,
  onLogout,
  onOpenProfile,
}) {
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'candidates', label: 'Candidates' },
    { id: 'transactions', label: 'Transactions' },
    { id: 'inventory', label: 'Inventory' },
    { id: 'demo', label: '⚡ Extractor' },
  ];

  return (
    <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50 px-6 py-4 flex flex-wrap items-center justify-between gap-4">
      {/* Brand & Enterprise Identity */}
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
          <p className="text-xs text-slate-400 flex flex-wrap items-center gap-1.5 mt-0.5">
            <Building2 className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-slate-200 font-semibold">{session?.business_name || 'My Enterprise'}</span>
            {session?.name && <span>• {session.name}</span>}
            {session?.phone_number && (
              <span className="inline-flex items-center gap-1 text-emerald-400 font-mono bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">
                <Phone className="w-3 h-3 text-emerald-500" />
                {session.phone_number}
              </span>
            )}
            {session?.email && <span className="text-slate-500">({session.email})</span>}
          </p>
        </div>
      </div>

      {/* Navigation Controls */}
      <div className="flex items-center space-x-2 sm:space-x-3">
        {/* Tab Switcher */}
        <div className="bg-slate-800/80 p-1 rounded-lg border border-slate-700/60 flex space-x-1 text-xs font-medium">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded-md transition-all ${
                activeTab === tab.id
                  ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                  : 'text-slate-300 hover:text-white hover:bg-slate-700/50'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Database Refresh Button */}
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1 text-xs disabled:opacity-50"
          title="Refresh database state"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-emerald-400' : ''}`} />
        </button>

        {/* User Profile Badge */}
        <div
          className="flex items-center space-x-2 pl-2 border-l border-slate-800 cursor-pointer group"
          onClick={onOpenProfile}
          title="Click to view or edit profile details"
        >
          {session?.picture ? (
            <img
              src={session.picture}
              alt={session.name || 'User Profile'}
              className="w-7 h-7 rounded-full border border-emerald-500/40 object-cover shadow-sm group-hover:border-emerald-400 transition"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="w-7 h-7 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center justify-center font-bold text-xs group-hover:border-emerald-400 transition">
              {(session?.name || session?.email || 'U')[0].toUpperCase()}
            </div>
          )}
          <div className="hidden md:flex flex-col text-left">
            <span className="text-xs font-semibold text-white leading-tight group-hover:text-emerald-400 transition">
              {session?.name || 'User'}
            </span>
            <span className="text-[11px] text-emerald-400 font-mono leading-tight">
              {session?.phone_number || ''}
            </span>
          </div>
        </div>

        {/* Sign Out Button */}
        <button
          onClick={onLogout}
          className="px-3 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg border border-rose-500/20 transition text-xs font-semibold"
          title="Sign out"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
