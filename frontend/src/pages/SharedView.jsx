import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import { FileText, Lock } from "lucide-react";
import api from "@/lib/api";
import { sanitizeSearchText } from "@/lib/searchText";

export default function SharedView() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [lib, setLib] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

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

  useEffect(() => {
    if (!q.trim()) {
      setSearchResults(null);
      return undefined;
    }
    if (!lib) {
      setSearchResults([]);
      return undefined;
    }

    const safeQ = sanitizeSearchText(q);
    const libPdfIds = new Set((lib.pdfs || []).map((item) => String(item.id)));
    if (libPdfIds.size === 0) {
      setSearchResults([]);
      return undefined;
    }

    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const r = await api.get("/search", { params: { q: safeQ, pdf_ids: Array.from(libPdfIds).join(",") }, signal: ctrl.signal });
        const allResults = r.data.results || [];
        const filtered = allResults.filter((res) => libPdfIds.has(String(res.pdf_id || res.id)));
        if (mountedRef.current) setSearchResults(filtered);
      } catch (e) {
        if (mountedRef.current && e.name !== "CanceledError" && e.name !== "AbortError" && e.code !== "ERR_CANCELED") {
          setSearchResults([]);
        }
      }
    }, 350);

    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [q, lib]);

  if (error) return (
    <div className="max-w-2xl mx-auto p-12 text-center">
      <h2 className="font-display text-2xl font-bold mb-2">Link non disponibile</h2>
      <p className="text-muted2">{error}</p>
      <Link to="/" className="btn-primary mt-6 inline-block">Torna alla Home</Link>
    </div>
  );

  if (!lib) return <div className="p-12 text-mono text-sm text-muted2">Caricamento…</div>;

  const visiblePdfs = q.trim() ? (searchResults ?? []) : (lib.pdfs || []);

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12">
      <p className="overline mb-2">LIBRERIA CONDIVISA</p>
      <h1 className="font-display font-black text-4xl tracking-tighter mb-1">{lib.name}</h1>
      {lib.description && <p className="text-muted2 mb-6 max-w-2xl">{lib.description}</p>}
      
      <div className="public-view-banner border rounded-md p-4 mb-8 text-sm">
        <div className="flex gap-2">
          <Lock size={16} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-bold">Vista Pubblica</p>
            <p>Gli spartiti protetti sono visibili solo ai membri del gruppo Chiesa Pomigliano.</p>
            <Link to="/login" className="font-bold underline mt-2 inline-block hover:opacity-80">Accedi come Gruppo</Link>
          </div>
        </div>
      </div>

      <div className="mb-6">
        <label className="overline block mb-2">Cerca nel contenuto della condivisione</label>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Cerca parole presenti nei PDF..."
          className="w-full border border-rule rounded-md bg-card px-4 py-3 text-sm focus:outline-none focus:border-ink"
        />
      </div>

      <ul className="border-t border-rule">
        {visiblePdfs.length === 0 && (
          <li className="py-12 text-center text-muted2 text-sm">Nessun PDF disponibile pubblicamente{q ? " per la ricerca richiesta" : ""}.</li>
        )}
        {visiblePdfs.map((p, idx) => {
          if (q.trim()) {
            return (
              <li key={p.pdf_id || p.id || idx} className="py-4 border-b border-rule hover:bg-canvas2 px-2 -mx-2 transition-colors">
                <button
                  onClick={() => navigate(`/viewer/${p.pdf_id || p.id}?page=${encodeURIComponent(p.page_label || p.page || 1)}&q=${encodeURIComponent(safeQ)}`)}
                  className="text-left w-full flex items-start gap-4"
                >
                  <FileText size={18} className="text-muted2 mt-1 shrink-0" />
                  <div className="min-w-0">
                    <div className="font-display font-bold text-lg hover:underline decoration-2 underline-offset-4">{p.title}</div>
                    <div className="text-mono text-xs text-muted3 mt-1">PAGINA {p.page_label || p.page}</div>
                    {p.snippet && <p className="text-sm text-muted2 mt-2 leading-relaxed">{p.snippet}</p>}
                  </div>
                </button>
              </li>
            );
          }
          return (
            <li key={p.id || idx} className="py-4 border-b border-rule hover:bg-canvas2 px-2 -mx-2 transition-colors">
              <button onClick={() => navigate(`/viewer/${p.id}`)} className="text-left w-full flex items-start gap-4">
                <FileText size={18} className="text-muted2 mt-1 shrink-0" />
                <div className="min-w-0">
                  <div className="font-display font-bold text-lg hover:underline decoration-2 underline-offset-4">{p.title}</div>
                  <div className="text-mono text-xs text-muted3 mt-1">{p.pages || ""} pagine</div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
