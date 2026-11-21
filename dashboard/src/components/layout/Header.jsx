import React from 'react';
import { Building2, Phone, RefreshCw, MessageSquare } from 'lucide-react';

function WhatsAppIcon({ className = "w-4 h-4" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
    </svg>
  );
}

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

  const WHATSAPP_REDIRECT_URL = "https://wa.me/15551737641";
  const businessName = session?.business_name || 'My Business';

  return (
    <header className="border-b border-slate-800 bg-slate-900/95 backdrop-blur sticky top-0 z-50 px-3 sm:px-6 py-2.5 sm:py-3.5">
      {/* Top Row: Brand, Enterprise info, WhatsApp Action, Profile, Refresh & Logout */}
      <div className="flex items-center justify-between gap-2 sm:gap-4 flex-wrap">
        {/* Brand identity */}
        <div className="flex items-center space-x-2.5 min-w-0">
          <div className="h-9 w-9 sm:h-10 sm:w-10 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 text-lg sm:text-xl shadow-md shadow-emerald-500/20 shrink-0">
            ⚡
          </div>
          <div className="min-w-0">
            <div className="flex items-center space-x-1.5 sm:space-x-2">
              <h1 className="font-bold text-sm sm:text-base md:text-lg text-white tracking-tight truncate">
                Clandexis Invisible ERP
              </h1>
              <span className="hidden sm:inline-block px-2 py-0.5 text-[10px] sm:text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Live
              </span>
            </div>
            <p className="text-[11px] sm:text-xs text-slate-400 flex items-center gap-1.5 mt-0.5 truncate">
              <Building2 className="w-3 h-3 text-slate-500 shrink-0" />
              <span className="text-slate-200 font-semibold truncate max-w-[120px] sm:max-w-[180px] md:max-w-none">
                {session?.business_name || 'My Enterprise'}
              </span>
              {session?.phone_number && (
                <span className="hidden md:inline-flex items-center gap-1 text-emerald-400 font-mono bg-emerald-500/10 px-1.5 py-0.2 rounded border border-emerald-500/20 text-[10px]">
                  <Phone className="w-2.5 h-2.5 text-emerald-500" />
                  {session.phone_number}
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Action Controls & Navigation */}
        <div className="flex items-center space-x-1.5 sm:space-x-2.5 shrink-0 ml-auto">
          {/* WhatsApp Direct Chat Redirect Button */}
          <a
            href={WHATSAPP_REDIRECT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg bg-[#25D366] hover:bg-[#20bd5a] text-slate-950 font-bold text-xs shadow-md shadow-[#25D366]/20 transition shrink-0"
            title={`Chat directly on WhatsApp with ${businessName}`}
          >
            <WhatsAppIcon className="w-4 h-4 fill-slate-950 shrink-0" />
            <span>WhatsApp</span>
            <span className="hidden sm:inline text-[11px] font-medium text-slate-900 max-w-[140px] truncate">
              • {businessName}
            </span>
          </a>

          {/* Database Refresh Button */}
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="p-1.5 sm:p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1 text-xs disabled:opacity-50"
            title="Refresh database"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-emerald-400' : ''}`} />
          </button>

          {/* User Profile Avatar / Settings */}
          <div
            className="flex items-center space-x-1.5 pl-1.5 sm:pl-2 border-l border-slate-800 cursor-pointer group"
            onClick={onOpenProfile}
            title="Click to view or edit profile details"
          >
            {session?.picture ? (
              <img
                src={session.picture}
                alt={session.name || 'User Profile'}
                className="w-7 h-7 rounded-full border border-emerald-500/40 object-cover shadow-sm group-hover:border-emerald-400 transition shrink-0"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="w-7 h-7 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center justify-center font-bold text-xs group-hover:border-emerald-400 transition shrink-0">
                {(session?.name || session?.email || 'U')[0].toUpperCase()}
              </div>
            )}
            <div className="hidden lg:flex flex-col text-left">
              <span className="text-xs font-semibold text-white leading-tight group-hover:text-emerald-400 transition truncate max-w-[90px]">
                {session?.name || 'User'}
              </span>
              <span className="text-[10px] text-emerald-400 font-mono leading-tight truncate">
                {session?.phone_number || ''}
              </span>
            </div>
          </div>

          {/* Sign Out Button */}
          <button
            onClick={onLogout}
            className="px-2.5 sm:px-3 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg border border-rose-500/20 transition text-xs font-semibold"
            title="Sign out"
          >
            Exit
          </button>
        </div>
      </div>

      {/* Second Row: Mobile-Responsive Tab Switcher (Horizontally scrollable on small screens) */}
      <div className="mt-2.5 pt-2 border-t border-slate-800/60 overflow-x-auto no-scrollbar">
        <div className="flex items-center space-x-1 min-w-max">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
