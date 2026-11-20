import React, { useState } from 'react';
import { FileText, RefreshCw, Upload } from 'lucide-react';
import { extractTransaction } from '../../api/dashboardApi';

export default function ExtractorTab({
  session,
  merchantId,
  onExtractionSuccess,
  onNavigateTab,
}) {
  const [demoText, setDemoText] = useState('');
  const [audioFile, setAudioFile] = useState(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoResult, setDemoResult] = useState(null);

  const handleDemoExtract = async (e) => {
    e.preventDefault();
    setDemoLoading(true);
    setDemoResult(null);

    try {
      const data = await extractTransaction({
        merchantId,
        businessName: session?.business_name,
        audioFile,
        demoText,
      });
      setDemoResult(data);
      if (onExtractionSuccess) onExtractionSuccess();
    } catch (err) {
      setDemoResult({ error: err.message });
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="border border-slate-800 rounded-xl p-6 bg-slate-900 space-y-4 shadow-sm">
        <div>
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <FileText className="w-5 h-5 text-emerald-400" />
            Live Multimodal Transaction Extractor
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Submit vernacular voice audio, invoice text, or WhatsApp messages directly to the Schema 1.1 extraction engine and Stage 4 validator.
          </p>
        </div>

        <form onSubmit={handleDemoExtract} className="space-y-4">
          {/* Audio File Input */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1 flex items-center gap-1.5">
              <Upload className="w-3.5 h-3.5 text-emerald-400" />
              Upload Audio (Voice note - takes precedence over text):
            </label>
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => setAudioFile(e.target.files ? e.target.files[0] : null)}
              className="block w-full text-xs text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-emerald-500/10 file:text-emerald-400 hover:file:bg-emerald-500/20 cursor-pointer"
            />
          </div>

          {/* Vernacular Raw Text Input */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Merchant Raw Input (Telugu, Hindi, English, Hinglish):
            </label>
            <textarea
              rows={4}
              value={demoText}
              onChange={(e) => setDemoText(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 focus:outline-none focus:border-emerald-500 transition"
              placeholder="e.g. Ramesh ki 50 shirts 200 each ki ammamu. 5000 cash, 5000 udhar."
            />
          </div>

          <div className="flex items-center justify-between pt-1">
            <div className="flex items-center space-x-2 text-xs text-slate-400">
              <span className="bg-slate-800 px-2 py-1 rounded">Gemini</span>
              <span className="bg-slate-800 px-2 py-1 rounded">Decimal Arithmetic</span>
            </div>
            <button
              type="submit"
              disabled={demoLoading}
              className="px-5 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs transition flex items-center gap-2 shadow-sm disabled:opacity-50"
            >
              {demoLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : 'Run Extraction & Validation'}
            </button>
          </div>
        </form>

        {/* Extractor Result Display */}
        {demoResult && (
          <div className="mt-4 pt-4 border-t border-slate-800 space-y-3">
            <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
              Extraction & Validation Response
            </h3>

            {demoResult.staged_candidates && demoResult.staged_candidates.length > 0 && (
              <div className="space-y-2">
                {demoResult.staged_candidates.map((row) => (
                  <div
                    key={row.candidate_id}
                    className="p-3 bg-slate-950/70 border border-slate-800 rounded-lg text-xs flex flex-wrap items-center justify-between gap-2"
                  >
                    <div>
                      <code className="text-emerald-400 font-mono text-[11px]">{row.candidate_id}</code>
                      <span className="text-slate-400 mx-2">·</span>
                      <span className="text-slate-300">{row.validation_status}</span>
                      <span className="text-slate-400 mx-2">·</span>
                      <span className="font-semibold text-white">{row.posting_status}</span>
                      {row.posting_status === 'NEEDS_REVIEW' && (
                        <p className="text-amber-300 mt-1">
                          {demoResult.extraction?.transactions?.[row.candidate_index]?.clarification_question}{' '}
                          <button
                            className="underline text-sky-400 hover:text-sky-300 ml-1 font-semibold"
                            onClick={() => onNavigateTab && onNavigateTab('overview')}
                          >
                            Answer in Overview
                          </button>
                        </p>
                      )}
                    </div>
                    <button
                      className="underline text-sky-400 hover:text-sky-300 font-medium"
                      onClick={() => onNavigateTab && onNavigateTab('candidates')}
                    >
                      View in Candidates Queue →
                    </button>
                  </div>
                ))}
              </div>
            )}

            <pre className="p-4 bg-slate-950 rounded-lg border border-slate-800 text-[11px] text-emerald-400 overflow-x-auto font-mono max-h-96">
              {JSON.stringify(demoResult, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
