import React, { useEffect, useState, useRef, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ZoomIn, ZoomOut, Maximize2, Star, ChevronUp, ChevronDown, Eye, EyeOff, Cloud, HardDrive } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { toast } from "sonner";
import api, { API } from "@/lib/api";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

export default function PdfViewer() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const initialPage = parseInt(params.get("page") || "1", 10);
  const initialQuery = params.get("q") || "";
  const navigate = useNavigate();

  const [meta, setMeta] = useState(null);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.2);
  const [containerWidth, setContainerWidth] = useState(800);
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(null);
  const [queryStr, setQueryStr] = useState(initialQuery);
  const [highlightsHidden, setHighlightsHidden] = useState(false);
  const [matches, setMatches] = useState([]); // array of HTMLElement
  const [matchIndex, setMatchIndex] = useState(0);
  const [renderedPages, setRenderedPages] = useState(0);

  const containerRef = useRef(null);
  const pageRefs = useRef({});
  const token = localStorage.getItem("scorelib_token");
  const fileUrl = `${API}/pdfs/${id}/file?token=${encodeURIComponent(token || "")}`;
  const fileObj = React.useMemo(() => ({ url: fileUrl }), [fileUrl]);

  useEffect(() => {
    api.get(`/pdfs/${id}`).then((r) => setMeta(r.data)).catch((e) => setError(e.response?.data?.detail || "PDF non trovato"));
  }, [id]);

  useEffect(() => {
    const update = () => {
      if (containerRef.current) setContainerWidth(Math.min(containerRef.current.clientWidth - 32, 1000));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const onDocumentLoad = ({ numPages: n }) => {
    setNumPages(n);
    setBusy(false);
  };

  // Highlight matched query inside text layer
  const customTextRenderer = useCallback(({ str }) => {
    if (!queryStr || !str) return str;
    try {
      const re = new RegExp(`(${queryStr.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
      return str.replace(re, (m) => `<mark class="hl">${m}</mark>`);
    } catch { return str; }
  }, [queryStr]);

  // Re-collect all matches whenever every page is rendered or query changes
  const collectMatches = useCallback(() => {
    if (!containerRef.current) return;
    const list = Array.from(containerRef.current.querySelectorAll("mark.hl"));
    setMatches(list);
    setMatchIndex(0);
    if (list.length > 0) {
      // jump to first match (but respect initial page query if provided)
      setTimeout(() => scrollToMatch(list, 0), 50);
    }
  }, []);

  const onPageRender = () => {
    setRenderedPages((n) => n + 1);
  };

  // when all pages rendered or query changed, recollect
  useEffect(() => {
    if (numPages > 0 && renderedPages >= numPages) {
      collectMatches();
    }
    // eslint-disable-next-line
  }, [renderedPages, numPages, queryStr]);

  // jump to initialPage on first render
  useEffect(() => {
    if (numPages > 0 && initialPage > 1) {
      setTimeout(() => {
        const el = pageRefs.current[initialPage];
        if (el) el.scrollIntoView({ behavior: "auto", block: "start" });
      }, 200);
    }
    // eslint-disable-next-line
  }, [numPages]);

  const scrollToMatch = (list, idx) => {
    const node = list[idx];
    if (!node) return;
    list.forEach((n) => n.classList.remove("hl-active"));
    node.classList.add("hl-active");
    node.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const goPrev = () => {
    if (matches.length === 0) return;
    const i = (matchIndex - 1 + matches.length) % matches.length;
    setMatchIndex(i); scrollToMatch(matches, i);
  };
  const goNext = () => {
    if (matches.length === 0) return;
    const i = (matchIndex + 1) % matches.length;
    setMatchIndex(i); scrollToMatch(matches, i);
  };

  // keyboard nav
  useEffect(() => {
    const onKey = (e) => {
      if (document.activeElement?.tagName === "INPUT") return;
      if (e.key === "n" || e.key === "ArrowDown" && e.altKey) { e.preventDefault(); goNext(); }
      if (e.key === "N" || e.key === "ArrowUp" && e.altKey) { e.preventDefault(); goPrev(); }
      if (e.key === "Escape") setHighlightsHidden((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [matches, matchIndex]); // eslint-disable-line

  // Track current page on scroll
  useEffect(() => {
    const onScroll = () => {
      const scrollY = window.scrollY + 120;
      let cur = 1;
      for (let i = 1; i <= numPages; i++) {
        const el = pageRefs.current[i];
        if (el && el.offsetTop <= scrollY) cur = i; else break;
      }
      setCurrentPage(cur);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [numPages]);

  const goToPage = (n) => { const el = pageRefs.current[n]; if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); };

  const toggleFavorite = async () => {
    if (!meta) return;
    try { const r = await api.patch(`/pdfs/${id}`, { is_favorite: !meta.is_favorite }); setMeta(r.data); toast.success(r.data.is_favorite ? "Aggiunto ai preferiti" : "Rimosso dai preferiti"); }
    catch { toast.error("Errore"); }
  };

  const clearQuery = () => { setQueryStr(""); setMatches([]); setMatchIndex(0); };

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-12 text-center" data-testid="pdf-viewer-error">
        <p className="text-lg font-display mb-3">{error}</p>
        <button onClick={() => navigate(-1)} className="btn-ghost border border-rule rounded-sm px-4 py-2">← Indietro</button>
      </div>
    );
  }

  return (
    <div className={`min-h-screen flex flex-col bg-canvas3 ${highlightsHidden ? "hl-off" : ""}`} data-testid="pdf-viewer-page">
      {/* Sticky toolbar */}
      <div className="sticky top-0 z-30 bg-white/95 backdrop-blur-xl border-b border-rule px-4 md:px-6 py-2.5 flex items-center gap-3 flex-wrap">
        <button onClick={() => navigate(-1)} className="btn-ghost shrink-0" data-testid="viewer-back-btn">
          <ArrowLeft size={16} /> <span className="hidden sm:inline">Indietro</span>
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-display font-semibold truncate">{meta?.title || "PDF"}</span>
            {meta && (
              <button onClick={toggleFavorite} className="btn-ghost shrink-0" data-testid="viewer-favorite-btn" title="Preferito">
                <Star size={16} fill={meta.is_favorite ? "#0A0A0A" : "none"} strokeWidth={1.5} />
              </button>
            )}
            {meta?.storage_type && (
              <span className="text-mono text-[10px] px-2 py-0.5 rounded-sm border border-rule text-muted2 inline-flex items-center gap-1" data-testid="viewer-storage-badge" title={meta.storage_type === "google_drive" ? `Drive · ${meta.drive_file_id}` : `Locale · ${meta.file_path}`}>
                {meta.storage_type === "google_drive" ? <><Cloud size={10} /> DRIVE</> : <><HardDrive size={10} /> LOCALE</>}
              </span>
            )}
          </div>
          <div className="text-mono text-xs text-muted2 flex flex-wrap items-center gap-2 mt-0.5">
            <span>Pag <input type="number" min={1} max={numPages || 1} value={currentPage} onChange={(e) => { const n = parseInt(e.target.value || "1", 10); setCurrentPage(n); goToPage(n); }} className="w-12 bg-canvas2 border border-rule rounded-sm px-1 py-0.5 text-center" data-testid="viewer-page-input" /> / {numPages || "…"}</span>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => setScale((s) => Math.max(0.5, s - 0.15))} className="btn-ghost" data-testid="viewer-zoom-out"><ZoomOut size={16} /></button>
          <span className="text-mono text-xs text-muted2 w-10 text-center">{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale((s) => Math.min(3, s + 0.15))} className="btn-ghost" data-testid="viewer-zoom-in"><ZoomIn size={16} /></button>
          <button onClick={() => setScale(1.2)} className="btn-ghost hidden sm:inline-flex" data-testid="viewer-zoom-reset" title="Reset zoom"><Maximize2 size={14} /></button>
        </div>
      </div>

      {/* Search/match bar (sticky just below toolbar) */}
      {queryStr && (
        <div className="sticky top-[57px] z-20 bg-highlight/90 backdrop-blur border-b border-[#FDE047] px-4 md:px-6 py-2 flex items-center gap-2 flex-wrap" data-testid="viewer-search-bar">
          <span className="text-mono text-xs text-highlightFg uppercase tracking-wider shrink-0">Cerca</span>
          <span className="font-medium text-highlightFg truncate max-w-xs">"{queryStr}"</span>
          <span className="text-mono text-xs text-highlightFg shrink-0" data-testid="match-counter">
            {matches.length === 0 ? "Nessun risultato" : `Risultato ${matchIndex + 1} di ${matches.length}`}
          </span>
          <div className="flex items-center gap-1 ml-auto shrink-0">
            <button onClick={goPrev} disabled={matches.length === 0} className="px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50 disabled:opacity-30" title="Precedente" data-testid="match-prev">
              <ChevronUp size={14} />
            </button>
            <button onClick={goNext} disabled={matches.length === 0} className="px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50 disabled:opacity-30" title="Successivo (n)" data-testid="match-next">
              <ChevronDown size={14} />
            </button>
            <button onClick={() => setHighlightsHidden((v) => !v)} className="px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50" title="Mostra/Nascondi evidenziazione (Esc)" data-testid="toggle-highlights">
              {highlightsHidden ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button onClick={clearQuery} className="ml-1 px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50 text-xs font-mono uppercase tracking-wider" title="Cancella ricerca" data-testid="clear-search">
              ×
            </button>
          </div>
        </div>
      )}

      <div ref={containerRef} className="flex-1 flex flex-col items-center py-8 px-2 md:px-4">
        {busy && <div className="text-mono text-sm text-muted2 py-12" data-testid="pdf-loading">Caricamento PDF…</div>}
        <Document
          file={fileObj}
          onLoadSuccess={onDocumentLoad}
          onLoadError={(e) => { setError("Impossibile caricare il PDF"); setBusy(false); console.error(e); }}
          loading=""
          error=""
          className="w-full flex flex-col items-center gap-6"
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((pn) => (
            <div
              key={pn}
              ref={(el) => { if (el) pageRefs.current[pn] = el; }}
              className="bg-white shadow-md border border-rule"
              data-testid={`pdf-page-${pn}`}
            >
              <Page
                pageNumber={pn}
                width={containerWidth}
                scale={scale}
                renderTextLayer={true}
                renderAnnotationLayer={false}
                customTextRenderer={customTextRenderer}
                onRenderSuccess={onPageRender}
              />
              <div className="text-center text-mono text-xs text-muted3 py-1.5 border-t border-rule">PAG {pn}</div>
            </div>
          ))}
        </Document>
      </div>
    </div>
  );
}
