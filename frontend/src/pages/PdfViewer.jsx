import React, { useEffect, useState, useRef, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ZoomIn, ZoomOut, Maximize2, Star } from "lucide-react";
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
  const queryStr = params.get("q") || "";
  const navigate = useNavigate();

  const [meta, setMeta] = useState(null);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.2);
  const [containerWidth, setContainerWidth] = useState(800);
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(null);

  const containerRef = useRef(null);
  const pageRefs = useRef({});
  const token = localStorage.getItem("scorelib_token");
  const fileUrl = `${API}/pdfs/${id}/file?token=${encodeURIComponent(token || "")}`;

  // Memoize file object so react-pdf doesn't re-fetch on every render
  const fileObj = React.useMemo(() => ({ url: fileUrl }), [fileUrl]);

  useEffect(() => {
    api.get(`/pdfs/${id}`).then((r) => setMeta(r.data)).catch((e) => {
      setError(e.response?.data?.detail || "PDF non trovato");
    });
  }, [id]);

  useEffect(() => {
    const update = () => {
      if (containerRef.current) {
        setContainerWidth(Math.min(containerRef.current.clientWidth - 32, 1000));
      }
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const onDocumentLoad = ({ numPages: n }) => {
    setNumPages(n);
    setBusy(false);
    // jump to initial page after render frame
    setTimeout(() => {
      const el = pageRefs.current[initialPage];
      if (el) el.scrollIntoView({ behavior: "auto", block: "start" });
    }, 250);
  };

  // Highlight matched query inside text layer
  const customTextRenderer = useCallback(
    ({ str }) => {
      if (!queryStr || !str) return str;
      try {
        const re = new RegExp(`(${queryStr.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
        return str.replace(re, (m) => `<mark class="hl">${m}</mark>`);
      } catch { return str; }
    },
    [queryStr]
  );

  // Track current page on scroll
  useEffect(() => {
    const onScroll = () => {
      const scrollY = window.scrollY + 120;
      let cur = 1;
      for (let i = 1; i <= numPages; i++) {
        const el = pageRefs.current[i];
        if (el && el.offsetTop <= scrollY) cur = i;
        else break;
      }
      setCurrentPage(cur);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [numPages]);

  const goToPage = (n) => {
    const el = pageRefs.current[n];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const toggleFavorite = async () => {
    if (!meta) return;
    try {
      const r = await api.patch(`/pdfs/${id}`, { is_favorite: !meta.is_favorite });
      setMeta(r.data);
      toast.success(r.data.is_favorite ? "Aggiunto ai preferiti" : "Rimosso dai preferiti");
    } catch (e) { toast.error("Errore"); }
  };

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-12 text-center" data-testid="pdf-viewer-error">
        <p className="text-lg font-display mb-3">{error}</p>
        <button onClick={() => navigate(-1)} className="btn-ghost border border-rule rounded-sm px-4 py-2">← Indietro</button>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-canvas3" data-testid="pdf-viewer-page">
      {/* Sticky toolbar */}
      <div className="sticky top-0 z-30 bg-white/95 backdrop-blur-xl border-b border-rule px-4 md:px-6 py-2.5 flex items-center gap-3 flex-wrap">
        <button onClick={() => navigate(-1)} className="btn-ghost shrink-0" data-testid="viewer-back-btn">
          <ArrowLeft size={16} /> <span className="hidden sm:inline">Indietro</span>
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-display font-semibold truncate">{meta?.title || "PDF"}</span>
            {meta && (
              <button onClick={toggleFavorite} className="btn-ghost shrink-0" data-testid="viewer-favorite-btn" title="Preferito">
                <Star size={16} fill={meta.is_favorite ? "#0A0A0A" : "none"} strokeWidth={1.5} />
              </button>
            )}
          </div>
          <div className="text-mono text-xs text-muted2 flex flex-wrap items-center gap-2">
            <span>Pag <input type="number" min={1} max={numPages || 1} value={currentPage} onChange={(e) => { const n = parseInt(e.target.value || "1", 10); setCurrentPage(n); goToPage(n); }} className="w-12 bg-canvas2 border border-rule rounded-sm px-1 py-0.5 text-center" data-testid="viewer-page-input" /> / {numPages || "…"}</span>
            {queryStr && <span className="text-highlightFg bg-highlight px-2 py-0.5 rounded-sm" data-testid="viewer-search-badge">"{queryStr}"</span>}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => setScale((s) => Math.max(0.5, s - 0.15))} className="btn-ghost" data-testid="viewer-zoom-out"><ZoomOut size={16} /></button>
          <span className="text-mono text-xs text-muted2 w-10 text-center">{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale((s) => Math.min(3, s + 0.15))} className="btn-ghost" data-testid="viewer-zoom-in"><ZoomIn size={16} /></button>
          <button onClick={() => setScale(1.2)} className="btn-ghost hidden sm:inline-flex" data-testid="viewer-zoom-reset" title="Reset zoom"><Maximize2 size={14} /></button>
        </div>
      </div>

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
              />
              <div className="text-center text-mono text-xs text-muted3 py-1.5 border-t border-rule">PAG {pn}</div>
            </div>
          ))}
        </Document>
      </div>
    </div>
  );
}
