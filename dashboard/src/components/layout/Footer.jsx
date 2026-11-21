import React from 'react';

export default function Footer() {
  return (
    <footer className="border-t border-slate-800 bg-slate-900/60 py-3 px-4 sm:px-6 text-center text-xs text-slate-500">
      <div className="flex flex-col sm:flex-row items-center justify-between gap-2 max-w-7xl mx-auto">
        <div className="truncate">
          Clandexis Invisible ERP • Startup Buildathon India 2026
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <a
            href="https://wa.me/919848892720"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#25D366] hover:underline font-medium"
          >
            Chat with Clandexis ERP on WhatsApp
          </a>
          <span>•</span>
          <span className="font-mono text-slate-400">asia-south1</span>
        </div>
      </div>
    </footer>
  );
}
