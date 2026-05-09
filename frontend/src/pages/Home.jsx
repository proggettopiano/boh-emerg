import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Search as SearchIcon, Upload as UploadIcon } from "lucide-react";
import api from "@/lib/api";
import UploadModal from "@/components/UploadModal";
import TrebleClef from "@/components/TrebleClef";

function highlight(text, q) {
  if (!text || !q) return text;
  try {
    const re = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
    const parts = text.split(re);
    return parts.map((p, i) => (re.test(p) ? <mark key={i} className="hl">{p}</mark> : <span key={i}>{p}</span>));
  } catch { return text; }
}

export default function Home() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [count, setCount] = useState(0);
  const [openUpload, setOpenUpload] = useState(false);
  const tref = useRef(null);
  const navigate = useNavigate();
  const inputRef = useRef(null);

  useEffect(() => {
    let alive = true;
    api.get("/pdfs").then((r) => { if (alive) setCount(r.data.items.length); }).catch(() => {});
    const onKey = (e) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || e.key === "/") {
        if (document.activeElement?.tagName === "INPUT") return;
        e.preventDefault(); inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { alive = false; window.removeEventListener("keydown", onKey); };
  }, []);

  useEffect(() => {
    if (tref.current) clearTimeout(tref.current);
    if (!q.trim()) { setResults(null); return; }
    const ctrl = new AbortController();
    tref.current = setTimeout(async () => {
      try {
        const r = await api.get(`/search`, { params: { q }, signal: ctrl.signal });
        setResults(r.data.results);
      } catch (e) {
        if (e.name !== "CanceledError" && e.name !== "AbortError") setResults([]);
      }
    }, 350);
    return () => { clearTimeout(tref.current); ctrl.abort(); };
  }, [q]);

  return (
    <div className="max-w-4xl mx-auto px-6 md:px-12 py-12 md:py-20">
      <div className="text-center mb-10" data-testid="home-hero">
        <div className="inline-block mb-6"><TrebleClef size={44} /></div>
        <h1 className="font-display font-black text-4xl sm:text-5xl lg:text-6xl tracking-tighter mb-3">
          Trova ogni spartito.<br />In un battito.
        </h1>
        <p className="text-[#525252]">Premi <kbd className="text-mono text-xs border border-rule rounded px-1.5 py-0.5">/</kbd> per cercare ovunque.</p>
      </div>

      <div className="bg-white border-2 border-ink rounded-md mb-3" style={{ boxShadow: "0 6px 0 0 #0A0A0A" }}>
        <div className="flex items-center gap-3 px-5">
          <SearchIcon size={20} className="text-muted2" strokeWidth={1.75} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cerca per titolo, contenuto, accordi…"
            className="w-full text-xl md:text-2xl py-5 outline-none placeholder:text-muted3 bg-transparent"
            data-testid="global-search-input"
          />
          {q && <button onClick={() => setQ("")} className="text-mono text-xs text-muted2 hover:text-ink" data-testid="search-clear">CANCELLA</button>}
        </div>
      </div>

      <div className="flex items-center justify-between text-sm text-muted2 mb-8">
        <span data-testid="library-count">{count} PDF nella tua libreria</span>
        <button onClick={() => setOpenUpload(true)} className="btn-primary !py-2 !px-4 text-sm" data-testid="home-upload-btn">
          <UploadIcon size={16} /> Carica PDF
        </button>
      </div>

      {results && (
        <ul className="border-t border-rule" data-testid="search-results">
          {results.length === 0 && (
            <li className="py-10 text-center text-muted2" data-testid="search-empty">Nessun risultato per "<span className="text-ink">{q}</span>"</li>
          )}
          {results.map((r) => (
            <li key={r.pdf_id + ":" + r.page} className="py-5 border-b border-rule animate-fade-in">
              <button
                onClick={() => navigate(`/viewer/${r.pdf_id}?page=${r.page}&q=${encodeURIComponent(q)}`)}
                className="text-left w-full group"
                data-testid={`search-result-${r.pdf_id}`}
              >
                <div className="flex items-baseline gap-3 flex-wrap mb-1">
                  <span className="font-display text-xl font-semibold group-hover:underline decoration-2 underline-offset-4">{highlight(r.title, q)}</span>
                  <span className="text-mono text-xs px-2 py-0.5 border border-rule rounded-sm text-muted2">
                    {r.source === "personal" ? "PERSONALE" : `CONDIVISA · ${r.source.replace("shared:", "")}`}
                  </span>
                  <span className="text-mono text-xs text-muted2">PAG {r.page}</span>
                </div>
                {r.snippet && <p className="text-[#525252] leading-relaxed">{highlight(r.snippet, q)}</p>}
                <p className="text-mono text-xs text-muted3 mt-1">{r.created_at?.slice(0, 10)}</p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <UploadModal open={openUpload} onClose={() => setOpenUpload(false)} onComplete={() => api.get("/pdfs").then((r) => setCount(r.data.items.length))} />
    </div>
  );
}
