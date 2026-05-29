import React, { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { UploadCloud, X, FileText, CheckCircle2, AlertCircle } from "lucide-react";
import api from "@/lib/api";

function makeFileId(file) {
  const randomPart = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${file.name}-${file.size}-${file.lastModified}-${randomPart}`;
}

function resultKey(result) {
  return result.client_key || result.pdf_id || result.existing_id || `${result.name}-${result.error || "ok"}`;
}

export default function UploadModal({ open, onClose, onComplete, libraryId }) {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState(0);
  const abortRef = useRef(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      setBusy(false);
      setDrag(false);
    }
  }, [open]);

  const handleFiles = (list) => {
    const picked = Array.from(list || []);
    const valid = picked.filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    const invalid = picked.length - valid.length;
    if (invalid > 0) toast.error(`${invalid} file ignorati: carica solo PDF validi.`);
    setFiles((prev) => {
      const seen = new Set(prev.map(({ file }) => `${file.name}-${file.size}-${file.lastModified}`));
      const next = valid
        .filter((file) => !seen.has(`${file.name}-${file.size}-${file.lastModified}`))
        .map((file) => ({ id: makeFileId(file), file }));
      return [...prev, ...next];
    });
  };
  const remove = (id) => setFiles((p) => p.filter((entry) => entry.id !== id));

  const upload = async () => {
    if (!files.length) return;
    setBusy(true);
    setProgress(0);
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const fd = new FormData();
      files.forEach(({ file }) => fd.append("files", file));
      const r = await api.post("/pdfs/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
        signal: ctrl.signal,
        onUploadProgress: (evt) => {
          if (evt.total && mountedRef.current) setProgress(Math.round((evt.loaded / evt.total) * 100));
        },
      });
      if (!mountedRef.current || ctrl.signal.aborted) return;
      const uploadResults = (r.data.results || []).map((result) => ({
        ...result,
        client_key: result.pdf_id || result.existing_id || makeFileId({ name: result.name || "result", size: 0, lastModified: 0 }),
      }));
      setResults(uploadResults);
      const ok = uploadResults.filter((x) => x.ok).length;
      const fail = uploadResults.length - ok;
      if (libraryId && ok > 0) {
        const ids = uploadResults.filter((x) => x.ok).map((x) => x.pdf_id);
        await api.post(`/libraries/${libraryId}/pdfs`, { pdf_ids: ids }, { signal: ctrl.signal });
      }
      toast.success(`${ok} caricati${fail ? ` - ${fail} errori` : ""}`);
      onComplete?.();
    } catch (e) {
      if (e.name === "CanceledError" || e.name === "AbortError" || e.code === "ERR_CANCELED") return;
      toast.error(e.response?.data?.detail || "Errore upload");
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const close = () => {
    abortRef.current?.abort();
    setFiles([]);
    setResults(null);
    setProgress(0);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={close} data-testid="upload-modal">
      <div className="bg-white border border-rule rounded-md w-full max-w-2xl p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-6">
          <h2 className="font-display text-2xl font-bold tracking-tight">Carica PDF</h2>
          <button onClick={close} className="btn-ghost" data-testid="upload-close-btn"><X size={18} /></button>
        </div>
        {!results && (
          <>
            <label
              className={`block border-2 border-dashed rounded-md p-12 text-center cursor-pointer transition-colors ${drag ? "border-ink bg-canvas3" : "border-muted3 bg-canvas2 hover:bg-canvas3"}`}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}
              data-testid="upload-dropzone"
            >
              <UploadCloud size={36} strokeWidth={1.5} className="mx-auto mb-3 text-muted2" />
              <p className="font-medium mb-1">Trascina qui i tuoi PDF, o clicca per selezionare</p>
              <p className="text-sm text-muted2">Multipli, anche pesanti. OCR automatico per scansioni.</p>
              <input
                type="file"
                accept="application/pdf"
                multiple
                className="hidden"
                onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
                data-testid="upload-file-input"
              />
            </label>
            {files.length > 0 && (
              <ul className="mt-6 space-y-2 max-h-64 overflow-y-auto" data-testid="upload-file-list">
                {files.map(({ id, file }) => (
                  <li key={id} className="flex items-center justify-between p-3 border border-rule rounded-sm">
                    <div className="flex items-center gap-3 min-w-0"><FileText size={16} /><span className="truncate text-sm">{file.name}</span><span className="text-mono text-xs text-muted2 shrink-0">{(file.size / 1024).toFixed(0)} KB</span></div>
                    <button onClick={() => remove(id)} className="btn-ghost"><X size={14} /></button>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-6 flex justify-end gap-3">
              <button onClick={close} className="btn-ghost border border-rule rounded-sm px-4 py-2" data-testid="upload-cancel-btn">Annulla</button>
              <button onClick={upload} disabled={busy || !files.length} className="btn-primary disabled:opacity-40" data-testid="upload-start-btn">{busy ? "Caricamento..." : `Carica ${files.length || ""}`}</button>
            </div>
            {busy && (
              <div className="mt-4" data-testid="upload-progress">
                <div className="h-2 bg-canvas3 rounded-sm overflow-hidden border border-rule">
                  <div className="h-full bg-ink transition-all" style={{ width: `${progress}%` }} />
                </div>
                <p className="text-mono text-xs text-muted2 mt-2">{progress}% inviato. Poi OCR e indicizzazione.</p>
              </div>
            )}
          </>
        )}
        {results && (
          <div data-testid="upload-results">
            <ul className="space-y-2 max-h-80 overflow-y-auto">
              {results.map((r) => (
                <li key={resultKey(r)} className="flex items-center justify-between p-3 border border-rule rounded-sm">
                  <div className="flex items-center gap-3 min-w-0">
                    {r.ok ? <CheckCircle2 size={16} className="text-emerald-600" /> : (r.duplicate ? <AlertCircle size={16} className="text-amber-600" /> : <AlertCircle size={16} className="text-red-600" />)}
                    <span className="truncate text-sm">{r.name}</span>
                  </div>
                  <div className="text-mono text-xs text-muted2">
                    {r.ok ? `${r.pages}pp${r.ocr ? " - OCR" : ""}${r.compressed ? " - compresso" : ""}` : (r.duplicate ? "DUPLICATO - gia in libreria" : r.error)}
                  </div>
                </li>
              ))}
            </ul>
            <div className="mt-6 flex justify-end"><button onClick={close} className="btn-primary" data-testid="upload-done-btn">Fatto</button></div>
          </div>
        )}
      </div>
    </div>
  );
}
