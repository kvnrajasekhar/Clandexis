import React, { useState } from 'react';

function WhatsAppIcon({ className = "w-4 h-4" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
    </svg>
  );
}

export default function LoginView({
  googleClientId,
  onGoogleLogin,
  onDemoLogin,
  loginError,
}) {
  const [showDemoForm, setShowDemoForm] = useState(false);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 grid place-items-center p-3 sm:p-4">
      <div className="w-full max-w-sm p-6 sm:p-8 bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl space-y-5 text-center">
        {/* Brand Icon */}
        <div className="mx-auto h-12 w-12 rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 text-2xl shadow-lg shadow-emerald-500/20">
          ⚡
        </div>

        {/* Title */}
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">Clandexis Invisible ERP</h1>
          <p className="text-xs text-slate-400 mt-1">Sign in with Google to access your enterprise dashboard</p>
        </div>

        {/* Google Sign-In Button */}
        <div className="pt-1 flex flex-col items-center space-y-3 w-full">
          {googleClientId ? (
            <div id="google-signin-btn" className="min-h-[44px] flex justify-center items-center w-full">
              <div className="animate-pulse text-xs text-slate-500">Loading Google Sign-In...</div>
            </div>
          ) : (
            <button
              onClick={() => onGoogleLogin('test:indra.gottipati7@gmail.com')}
              className="w-full flex items-center justify-center gap-3 bg-white hover:bg-slate-100 text-slate-900 font-semibold py-2.5 px-4 rounded-xl shadow transition text-xs"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
              </svg>
              Sign in with Google
            </button>
          )}

          {loginError && <p role="alert" className="text-xs text-rose-400 font-medium">{loginError}</p>}
        </div>

        {/* WhatsApp Direct Chat Link Button */}
        <div className="pt-1">
          <a
            href="https://wa.me/15551737641"
            target="_blank"
            rel="noopener noreferrer"
            className="w-full flex items-center justify-center gap-2 bg-[#25D366] hover:bg-[#20bd5a] text-slate-950 font-bold py-2.5 px-4 rounded-xl text-xs shadow-md transition"
          >
            <WhatsAppIcon className="w-4 h-4 fill-slate-950" />
            <span>Chat with WhatsApp ERP (+1 555 173-7641)</span>
          </a>
        </div>

        {/* Demo Credentials Option */}
        <div className="pt-1">
          {!showDemoForm ? (
            <button
              onClick={() => setShowDemoForm(true)}
              className="text-xs text-slate-400 hover:text-slate-200 underline transition"
            >
              Sign in with Demo credentials
            </button>
          ) : (
            <form onSubmit={onDemoLogin} className="space-y-3 text-left bg-slate-950/70 p-3 rounded-xl border border-slate-800">
              <div>
                <label className="block text-[11px] text-slate-400 font-medium mb-1">Username</label>
                <input
                  name="username"
                  defaultValue="demo"
                  required
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 font-medium mb-1">Password</label>
                <input
                  type="password"
                  name="password"
                  placeholder="••••••••"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                />
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  type="submit"
                  className="flex-1 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold py-1.5 px-3 rounded-lg text-xs transition"
                >
                  Demo Sign-In
                </button>
                <button
                  type="button"
                  onClick={() => setShowDemoForm(false)}
                  className="text-slate-400 hover:text-slate-200 text-xs px-2"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>

        <div className="pt-3 border-t border-slate-800/80 text-[11px] text-slate-500">
          Secure, authenticated merchant session • Multi-tenant isolation
        </div>
      </div>
    </main>
  );
}
