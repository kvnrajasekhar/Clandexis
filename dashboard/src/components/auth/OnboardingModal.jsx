import React, { useState } from 'react';
import { Building2, Phone, User, Mail, ShieldCheck, RefreshCw } from 'lucide-react';

export default function OnboardingModal({ session, onComplete, onCancel }) {
  const [name, setName] = useState(session?.name || '');
  const [phoneNumber, setPhoneNumber] = useState(session?.phone_number || '');
  const [businessName, setBusinessName] = useState(session?.business_name && !session.business_name.includes("'s Business") ? session.business_name : '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    const cleanName = name.trim();
    const cleanBiz = businessName.trim();
    const cleanPhone = phoneNumber.trim();

    if (!cleanName) {
      setError('Please provide your profile name');
      return;
    }
    if (!cleanPhone) {
      setError('Please enter your mobile number. It is required for WhatsApp & transaction syncing.');
      return;
    }
    if (!cleanBiz) {
      setError('Please provide your business or shop name.');
      return;
    }

    setLoading(true);
    try {
      await onComplete({
        name: cleanName,
        phone_number: cleanPhone,
        business_name: cleanBiz,
      });
    } catch (err) {
      setError(err.message || 'Failed to save merchant profile. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm grid place-items-center p-3 sm:p-4 overflow-y-auto">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-5 sm:p-8 space-y-5 text-left relative my-auto max-h-[92vh] overflow-y-auto animate-in fade-in duration-200">
        {/* Header / Intro */}
        <div className="flex items-start gap-4">
          {session?.picture ? (
            <img
              src={session.picture}
              alt={session.name || 'User'}
              className="w-14 h-14 rounded-2xl border-2 border-emerald-500/40 object-cover shadow-lg"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 text-2xl shadow-lg shadow-emerald-500/20">
              ⚡
            </div>
          )}
          <div className="flex-1">
            <h2 className="text-lg font-bold text-white tracking-tight">Complete Your Enterprise Setup</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Welcome to Clandexis. Since Google OAuth accounts don't provide a phone number, please confirm your contact & business name to activate your ledger.
            </p>
          </div>
        </div>

        {/* Error Notification */}
        {error && (
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
            {error}
          </div>
        )}

        {/* Setup Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Email (OAuth Authenticated - Read Only) */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Mail className="w-3.5 h-3.5 text-slate-400" />
                Google Account Email
              </span>
              <span className="text-[10px] text-emerald-400 font-normal flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" /> OAuth Verified
              </span>
            </label>
            <input
              type="email"
              value={session?.email || ''}
              disabled
              className="w-full bg-slate-950/80 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-slate-400 cursor-not-allowed font-mono"
            />
          </div>

          {/* Profile Name (Editable) */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center gap-1.5">
              <User className="w-3.5 h-3.5 text-slate-400" />
              Owner / Profile Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Ramesh Kumar"
              required
              maxLength={120}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-emerald-500 transition"
            />
          </div>

          {/* Mobile Number (Required) */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Phone className="w-3.5 h-3.5 text-emerald-400" />
                Mobile Number <span className="text-rose-400">*</span>
              </span>
              <span className="text-[10px] text-slate-500">10 digits or E.164 (+91)</span>
            </label>
            <div className="relative">
              <input
                type="tel"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="+91 98765 43210 or 9876543210"
                required
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white font-mono placeholder:font-sans focus:outline-none focus:border-emerald-500 transition"
              />
            </div>
            <p className="text-[10px] text-slate-500 mt-1">
              Used for WhatsApp conversational ERP invoices and automated payment reminders.
            </p>
          </div>

          {/* Business / Enterprise Name (Required) */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center gap-1.5">
              <Building2 className="w-3.5 h-3.5 text-teal-400" />
              Business / Shop Name <span className="text-rose-400">*</span>
            </label>
            <input
              type="text"
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              placeholder="e.g. Sri Balaji Textiles"
              required
              maxLength={150}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-emerald-500 transition"
            />
            <p className="text-[10px] text-slate-500 mt-1">
              Displayed on generated invoices, PDF receipts, and customer ledger summaries.
            </p>
          </div>

          {/* Actions */}
          <div className="pt-2 flex items-center gap-3">
            <button
              type="submit"
              disabled={loading}
              className="flex-1 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-bold py-2.5 px-4 rounded-xl text-xs transition shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Saving & Setting Up Database...
                </>
              ) : (
                'Save & Open Dashboard ⚡'
              )}
            </button>

            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                className="px-3.5 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs transition"
              >
                Sign out
              </button>
            )}
          </div>
        </form>

        {/* Security badge */}
        <div className="text-center pt-2 border-t border-slate-800/80 text-[11px] text-slate-500">
          Encrypted tenant storage • Multi-tenant isolation in PostgreSQL
        </div>
      </div>
    </div>
  );
}
