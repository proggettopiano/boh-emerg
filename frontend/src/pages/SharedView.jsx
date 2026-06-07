import React, { useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import { FileText, Lock, ExternalLink } from "lucide-react";
import api from "@/lib/api";

export default function SharedView() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [lib, setLib] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    
    api.get(`/shared/${token}`, { signal: ctrl.signal }).then((r) => {
      if (alive) setLib(r.data);
    }).catch((e) => {
      if (!alive || e.name === "CanceledError" || e.name === "AbortError" || e.code === "ERR_CANCELED") return;
      setError(e.response?.data?.detail || "Errore");
    });

    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [token]);

  if (error) return (
    <div className="max-w-2xl mx-auto p-12 text-center">
      <h2 className="font-display text-2xl font-bold mb-2">Link non disponibile</h2>
      <p className="text-muted2">{error}</p>
      <Link to="/" className="btn-primary mt-6 inline-block">Torna alla Home</Link>
    </div>
  );
  
  if (!lib) return <div className="p-12 text-mono text-sm text-muted2">Caricamento…</div>;

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12">
      <p className="overline mb-2">LIBRERIA CONDIVISA</p>
      <h1 className="font-display font-black text-4xl tracking-tighter mb-1">{lib.name}</h1>
      {lib.description && <p className="text-muted2 mb-6 max-w-2xl">{lib.description}</p>}
      
      <div className="bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 rounded-md p-4 mb-8 text-sm text-amber-800 dark:text-amber-100">
        <div className="flex gap-2">
          <Lock size={16} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-bold">Vista Pubblica</p>
            <p>Gli spartiti protetti sono visibili solo ai membri del gruppo Chiesa Pomigliano.</p>
            <Link to="/login" className="font-bold underline mt-2 inline-block hover:opacity-80">Accedi come Gruppo</Link>
          </div>
        </div>
      </div>

      <ul className="border-t border-rule">
        {(lib.pdfs || []).length === 0 && (
          <li className="py-12 text-center text-muted2 text-sm">Nessun PDF disponibile pubblicamente.</li>
        )}
        {(lib.pdfs || []).map((p, idx) => (
          <li key={idx} className="flex items-center justify-between gap-3 py-4 border-b border-rule group hover:bg-canvas2 px-2 -mx-2 transition-colors">
            <div className="text-left flex-1 flex items-center gap-3 min-w-0">
              <FileText size={18} strokeWidth={1.5} className="text-muted2" />
              <div className="min-w-0">
                <div className="font-medium truncate text-lg">{p.title}</div>
                <div className="text-mono text-xs text-muted2">{p.pages} pagine</div>
              </div>
            </div>
            <a 
              href={`${api.defaults.baseURL}/pdfs/${p.id}/file`} 
              target="_blank" 
              rel="noopener noreferrer"
              className="btn-ghost border border-rule rounded-sm px-4 py-2 text-sm flex items-center gap-2"
            >
              <ExternalLink size={14} /> APRI PDF
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
