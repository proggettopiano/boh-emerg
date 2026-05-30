import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ZoomIn, ZoomOut, Maximize2, Star, ChevronUp, ChevronDown, Eye, EyeOff, Cloud, HardDrive } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { toast } from "sonner";
import api, { API } from "@/lib/api";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

const PAGE_GAP = 24;
const PAGE_FOOTER_H = 28;
const PAGE_ASPECT = 297 / 210;
const PAGE_BUFFER = 4;

const estimatePageHeight = (width, scale) =>
  Math.round(width * scale * PAGE_ASPECT + PAGE_FOOTER_H + PAGE_GAP);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isCanceled(error) {
  return error?.name === "CanceledError" || error?.name === "AbortError" || error?.code === "ERR_CANCELED";
}

function pageRangeAround(page, numPages, buffer = PAGE_BUFFER) {
  const p = Math.max(1, Math.min(page, numPages));
  return {
    start: Math.max(1, p - buffer),
    end: Math.min(numPages, p + buffer),
  };
}

function rangeFromScroll(scrollY, viewportHeight, slotHeight, numPages) {
  const top = scrollY + 120;
  const bottom = top + viewportHeight;
  return {
    start: Math.max(1, Math.floor(top / slotHeight) - PAGE_BUFFER),
    end: Math.min(numPages, Math.ceil(bottom / slotHeight) + PAGE_BUFFER),
  };
}

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
  const [matches, setMatches] = useState([]);
  const [matchIndex, setMatchIndex] = useState(0);
  const [pageHeight, setPageHeight] = useState(null);
  const [visibleRange, setVisibleRange] = useState({ start: 1, end: 12 });

  const containerRef = useRef(null);
  const pageRefs = useRef({});
  const mountedRef = useRef(false);
  const collectTimerRef = useRef(null);
  const scrollTimerRef = useRef(null);
  const visibleRangeRef = useRef({ start: 1, end: 12 });
  const initialScrollDoneRef = useRef(false);
  const renderGenerationRef = useRef(0);
  const [renderGeneration, setRenderGeneration] = useState(0);

  const token = localStorage.getItem("scorelib_token");
  const fileUrl = `${API}/pdfs/${id}/file?token=${encodeURIComponent(token || "")}`;
  const fileObj = useMemo(() => ({ url: fileUrl }), [fileUrl]);

  const slotHeight = pageHeight || estimatePageHeight(containerWidth, scale);
  const totalHeight = numPages > 0 ? numPages * slotHeight : 0;
  const mountedPageCount = numPages > 0 ? visibleRange.end - visibleRange.start + 1 : 0;

  const applyVisibleRange = useCallback((start, end) => {
    if (visibleRangeRef.current.start === start && visibleRangeRef.current.end === end) return;
    visibleRangeRef.current = { start, end };
    setVisibleRange({ start, end });
  }, []);

  const setRangeAround = useCallback(
    (page, buffer = PAGE_BUFFER + 2) => {
      if (numPages <= 0) return;
      const { start, end } = pageRangeAround(page, numPages, buffer);
      applyVisibleRange(start, end);
    },
    [numPages, applyVisibleRange],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(collectTimerRef.current);
      clearTimeout(scrollTimerRef.current);
      pageRefs.current = {};
    };
  }, []);

  useEffect(() => {
    clearTimeout(collectTimerRef.current);
    clearTimeout(scrollTimerRef.current);
    initialScrollDoneRef.current = false;
    setMeta(null);
    setNumPages(0);
    setBusy(true);
    setError(null);
    setMatches([]);
    setMatchIndex(0);
    setPageHeight(null);
    setCurrentPage(initialPage);
    pageRefs.current = {};
    visibleRangeRef.current = { start: 1, end: 12 };
    setVisibleRange({ start: 1, end: 12 });
  }, [id, initialPage]);

  useEffect(() => {
    const ctrl = new AbortController();
    api.get(`/pdfs/${id}`, { signal: ctrl.signal })
      .then((r) => {
        if (mountedRef.current) setMeta(r.data);
      })
      .catch((e) => {
        if (!isCanceled(e) && mountedRef.current) setError(e.response?.data?.detail || "PDF non trovato");
      });
    return () => ctrl.abort();
  }, [id]);

  useEffect(() => {
    const update = () => {
      if (!containerRef.current) return;
      const nextWidth = Math.max(280, Math.min(containerRef.current.clientWidth - 32, 1000));
      setContainerWidth((prev) => (Math.abs(prev - nextWidth) > 4 ? nextWidth : prev));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  useEffect(() => {
    renderGenerationRef.current += 1;
    setRenderGeneration(renderGenerationRef.current);
    setPageHeight(null);
    setMatches([]);
    setMatchIndex(0);
    clearTimeout(collectTimerRef.current);
    if (numPages > 0) setRangeAround(currentPage);
  }, [id, queryStr, scale, containerWidth]); // eslint-disable-line react-hooks/exhaustive-deps

  const onDocumentLoad = useCallback(({ numPages: loadedPages }) => {
    if (!mountedRef.current) return;
    const target = Math.max(1, Math.min(initialPage, loadedPages));
    const { start, end } = pageRangeAround(target, loadedPages, PAGE_BUFFER + 3);
    visibleRangeRef.current = { start, end };
    setVisibleRange({ start, end });
    setCurrentPage(target);
    setNumPages(loadedPages);
    setBusy(false);
  }, [initialPage]);

  const customTextRenderer = useCallback(({ str }) => {
    if (!queryStr || !str) return str;
    try {
      const re = new RegExp(`(${escapeRegExp(queryStr)})`, "ig");
      return str.replace(re, (match) => `<mark class="hl">${match}</mark>`);
    } catch (err) {
      console.error("[PdfViewer] Regex highlight failed:", { query: queryStr, error: err.message });
      return str;
    }
  }, [queryStr]);

  const scrollToMatch = useCallback((list, idx, behavior = "smooth") => {
    const node = list[idx];
    if (!node || !mountedRef.current) return;
    list.forEach((item) => item.classList.remove("hl-active"));
    node.classList.add("hl-active");
    node.scrollIntoView({ behavior, block: "center" });
  }, []);

  const collectMatches = useCallback(() => {
    if (!mountedRef.current || !containerRef.current || !queryStr) return;
    const list = Array.from(containerRef.current.querySelectorAll("mark.hl"));
    setMatches(list);
    setMatchIndex(0);
    clearTimeout(scrollTimerRef.current);
    if (list.length > 0) {
      scrollTimerRef.current = setTimeout(() => scrollToMatch(list, 0, "smooth"), 50);
    }
  }, [queryStr, scrollToMatch]);

  const onPageRender = useCallback((pageNumber, generation) => {
    if (!mountedRef.current || generation !== renderGenerationRef.current) return;
    const el = pageRefs.current[pageNumber];
    if (el?.offsetHeight > 0) {
      const measured = el.offsetHeight;
      setPageHeight((prev) => (prev && Math.abs(prev - measured) < 4 ? prev : measured));
    }
    if (queryStr) {
      clearTimeout(collectTimerRef.current);
      collectTimerRef.current = setTimeout(collectMatches, 120);
    }
  }, [collectMatches, queryStr]);

  useEffect(() => {
    if (!queryStr || numPages === 0) return undefined;
    collectTimerRef.current = setTimeout(collectMatches, 250);
    return () => clearTimeout(collectTimerRef.current);
  }, [queryStr, numPages, visibleRange, collectMatches]);

  const scrollToPage = useCallback(
    (page, behavior = "auto") => {
      const p = Math.max(1, Math.min(page, numPages || page));
      setRangeAround(p);
      requestAnimationFrame(() => {
        window.scrollTo({ top: Math.max(0, (p - 1) * slotHeight), behavior });
      });
    },
    [numPages, setRangeAround, slotHeight],
  );

  useEffect(() => {
    if (numPages > 0 && initialPage > 1 && !initialScrollDoneRef.current) {
      initialScrollDoneRef.current = true;
      scrollToPage(initialPage, "auto");
    }
  }, [numPages, initialPage, scrollToPage]);

  const goPrev = useCallback(() => {
    if (matches.length === 0) return;
    const nextIndex = (matchIndex - 1 + matches.length) % matches.length;
    setMatchIndex(nextIndex);
    scrollToMatch(matches, nextIndex);
  }, [matches, matchIndex, scrollToMatch]);

  const goNext = useCallback(() => {
    if (matches.length === 0) return;
    const nextIndex = (matchIndex + 1) % matches.length;
    setMatchIndex(nextIndex);
    scrollToMatch(matches, nextIndex);
  }, [matches, matchIndex, scrollToMatch]);

  useEffect(() => {
    const onKey = (e) => {
      if (document.activeElement?.tagName === "INPUT") return;
      if (e.key === "n") {
        e.preventDefault();
        goNext();
      } else if (e.key === "N") {
        e.preventDefault();
        goPrev();
      } else if (e.key === "Escape" && queryStr) {
        setHighlightsHidden((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goNext, goPrev, queryStr]);

  useEffect(() => {
    if (numPages <= 0) return undefined;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const scrollY = window.scrollY;
        const { start, end } = rangeFromScroll(scrollY, window.innerHeight, slotHeight, numPages);
        applyVisibleRange(start, end);
        const cur = Math.min(numPages, Math.max(1, Math.floor((scrollY + 120) / slotHeight) + 1));
        setCurrentPage((prev) => (prev === cur ? prev : cur));
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
    };
  }, [numPages, slotHeight, applyVisibleRange]);

  const goToPage = (pageNumber) => {
    const clamped = Math.max(1, Math.min(pageNumber, numPages || 1));
    setCurrentPage(clamped);
    scrollToPage(clamped, "smooth");
  };

  const toggleFavorite = async () => {
    if (!meta) return;
    try {
      const r = await api.patch(`/pdfs/${id}`, { is_favorite: !meta.is_favorite });
      if (!mountedRef.current) return;
      setMeta(r.data);
      toast.success(r.data.is_favorite ? "Aggiunto ai preferiti" : "Rimosso dai preferiti");
    } catch (err) {
      if (isCanceled(err)) return;
      console.error("[PdfViewer] Failed to toggle favorite:", { pdf_id: id, error: err.message });
      toast.error("Errore nel salvataggio del preferito");
    }
  };

  const clearQuery = () => {
    setQueryStr("");
    setMatches([]);
    setMatchIndex(0);
  };

  const visiblePageNumbers = useMemo(() => {
    if (numPages <= 0) return [];
    return Array.from(
      { length: visibleRange.end - visibleRange.start + 1 },
      (_, index) => visibleRange.start + index,
    );
  }, [numPages, visibleRange.start, visibleRange.end]);

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-12 text-center" data-testid="pdf-viewer-error">
        <p className="text-lg font-display mb-3">{error}</p>
        <button onClick={() => navigate(-1)} className="btn-ghost border border-rule rounded-sm px-4 py-2">Indietro</button>
      </div>
    );
  }

  return (
    <div className={`min-h-screen flex flex-col bg-canvas3 ${highlightsHidden ? "hl-off" : ""}`} data-testid="pdf-viewer-page">
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
            <span>Pag <input type="number" min={1} max={numPages || 1} value={currentPage} onChange={(e) => { const nextPage = parseInt(e.target.value || "1", 10); goToPage(nextPage); }} className="w-12 bg-canvas2 border border-rule rounded-sm px-1 py-0.5 text-center" data-testid="viewer-page-input" /> / {numPages || "…"}</span>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => setScale((value) => Math.max(0.5, value - 0.15))} className="btn-ghost" data-testid="viewer-zoom-out"><ZoomOut size={16} /></button>
          <span className="text-mono text-xs text-muted2 w-10 text-center">{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale((value) => Math.min(3, value + 0.15))} className="btn-ghost" data-testid="viewer-zoom-in"><ZoomIn size={16} /></button>
          <button onClick={() => setScale(1.2)} className="btn-ghost hidden sm:inline-flex" data-testid="viewer-zoom-reset" title="Reset zoom"><Maximize2 size={14} /></button>
        </div>
      </div>

      {queryStr && (
        <div className="fixed top-[57px] left-0 right-0 z-20 bg-highlight/90 backdrop-blur border-b border-[#FDE047] px-4 md:px-6 py-2 flex items-center gap-2 flex-wrap" data-testid="viewer-search-bar">
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
            <button onClick={() => setHighlightsHidden((value) => !value)} className="px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50" title="Mostra/Nascondi evidenziazione (Esc)" data-testid="toggle-highlights">
              {highlightsHidden ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button onClick={clearQuery} className="ml-1 px-2 py-1 rounded-sm border border-[#854D0E]/30 hover:bg-white/50 text-xs font-mono uppercase tracking-wider" title="Cancella ricerca" data-testid="clear-search">
              ×
            </button>
          </div>
        </div>
      )}

      <div ref={containerRef} className="flex-1 flex flex-col items-center py-8 px-2 md:px-4" style={{ paddingTop: queryStr ? "60px" : "32px" }}>
        {busy && <div className="text-mono text-sm text-muted2 py-12" data-testid="pdf-loading">Caricamento PDF…</div>}
        <Document
          key={id}
          file={fileObj}
          onLoadSuccess={onDocumentLoad}
          onLoadError={(e) => {
            if (!mountedRef.current) return;
            setError("Impossibile caricare il PDF");
            setBusy(false);
            console.error(e);
          }}
          loading=""
          error=""
          className="w-full flex flex-col items-center"
        >
          {numPages > 0 && (
            <div
              className="relative w-full max-w-full"
              style={{ height: totalHeight }}
              data-testid="pdf-virtual-scroll"
              data-mounted-pages={mountedPageCount}
              data-total-pages={numPages}
            >
              {visiblePageNumbers.map((pageNumber) => (
                <div
                  key={`${id}-${pageNumber}-${renderGeneration}`}
                  ref={(el) => {
                    if (el) pageRefs.current[pageNumber] = el;
                    else delete pageRefs.current[pageNumber];
                  }}
                  className="absolute left-0 right-0 mx-auto bg-white shadow-md border border-rule"
                  style={{
                    top: (pageNumber - 1) * slotHeight,
                    width: containerWidth,
                    maxWidth: "100%",
                  }}
                  data-testid={`pdf-page-${pageNumber}`}
                >
                  <Page
                    pageNumber={pageNumber}
                    width={containerWidth}
                    scale={scale}
                    renderTextLayer
                    renderAnnotationLayer={false}
                    customTextRenderer={customTextRenderer}
                    onRenderSuccess={() => onPageRender(pageNumber, renderGeneration)}
                    onRenderError={(e) => console.error("[PdfViewer] Page render failed:", { pdf_id: id, pageNumber, error: e.message })}
                    loading=""
                  />
                  <div className="text-center text-mono text-xs text-muted3 py-1.5 border-t border-rule">PAG {pageNumber}</div>
                </div>
              ))}
            </div>
          )}
        </Document>
      </div>
    </div>
  );
}
