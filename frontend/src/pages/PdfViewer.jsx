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
const TOOLBAR_OFFSET = 120;
const TOOLBAR_OFFSET_WITH_SEARCH = 132;

const estimatePageHeight = (width, scale) =>
  Math.round(width * scale * PAGE_ASPECT + PAGE_FOOTER_H + PAGE_GAP);

async function measureSlotHeight(pdf, pageNumber, width, scale) {
  const page = await pdf.getPage(pageNumber);
  const vp = page.getViewport({ scale: 1 });
  return Math.round((vp.height / vp.width) * width * scale + PAGE_FOOTER_H + PAGE_GAP);
}

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

function rangeFromScroll(scrollY, viewportHeight, slotHeight, numPages, toolbarOffset) {
  const top = scrollY + toolbarOffset;
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
  const [pageInput, setPageInput] = useState(String(initialPage));

  const containerRef = useRef(null);
  const pageRefs = useRef({});
  const pdfDocRef = useRef(null);
  const pendingScrollPageRef = useRef(null);
  const scrollToPageRef = useRef(null);
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
  const scrollToolbarOffset = queryStr ? TOOLBAR_OFFSET_WITH_SEARCH : TOOLBAR_OFFSET;

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
    setPageInput(String(initialPage));
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
    setMatches([]);
    setMatchIndex(0);
    clearTimeout(collectTimerRef.current);
    if (numPages > 0) setRangeAround(currentPage);

    const pdf = pdfDocRef.current;
    if (!pdf || numPages <= 0) {
      setPageHeight(null);
      return undefined;
    }

    let cancelled = false;
    (async () => {
      const h = await measureSlotHeight(pdf, 1, containerWidth, scale);
      if (cancelled || !mountedRef.current || h <= 0) return;
      setPageHeight(h);
      const target = pendingScrollPageRef.current || currentPage;
      if (target > 1) scrollToPageRef.current?.(target, "auto");
    })();

    return () => { cancelled = true; };
  }, [id, queryStr, scale, containerWidth]); // eslint-disable-line react-hooks/exhaustive-deps

  const onDocumentLoad = useCallback(async (pdf) => {
    if (!mountedRef.current) return;
    pdfDocRef.current = pdf;
    const loadedPages = pdf.numPages;
    const target = Math.max(1, Math.min(initialPage, loadedPages));
    const measureAt = target > 1 ? target : 1;

    try {
      const h = await measureSlotHeight(pdf, measureAt, containerWidth, scale);
      if (mountedRef.current && h > 0) setPageHeight(h);
    } catch (err) {
      console.error("[PdfViewer] Failed to measure page height:", err);
    }

    const { start, end } = pageRangeAround(target, loadedPages, PAGE_BUFFER + 3);
    visibleRangeRef.current = { start, end };
    setVisibleRange({ start, end });
    setCurrentPage(target);
    setPageInput(String(target));
    setNumPages(loadedPages);
    setBusy(false);
  }, [initialPage, containerWidth, scale]);

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

  const scrollToPage = useCallback(
    (page, behavior = "auto") => {
      const p = Math.max(1, Math.min(page, numPages || page));
      pendingScrollPageRef.current = p;
      setCurrentPage(p);
      setPageInput(String(p));
      setRangeAround(p);

      const tryScroll = (attempts = 0) => {
        if (!mountedRef.current) return;
        const el = pageRefs.current[p];
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - scrollToolbarOffset;
          window.scrollTo({ top: Math.max(0, top), behavior });
          pendingScrollPageRef.current = null;
          if (initialPage > 1 && p === initialPage) initialScrollDoneRef.current = true;
          return;
        }
        if (attempts < 80) {
          requestAnimationFrame(() => tryScroll(attempts + 1));
          return;
        }
        window.scrollTo({ top: Math.max(0, (p - 1) * slotHeight), behavior });
        pendingScrollPageRef.current = null;
        if (initialPage > 1 && p === initialPage) initialScrollDoneRef.current = true;
      };

      requestAnimationFrame(() => tryScroll());
    },
    [numPages, setRangeAround, slotHeight, scrollToolbarOffset, initialPage],
  );

  scrollToPageRef.current = scrollToPage;

  const collectMatches = useCallback(() => {
    if (!mountedRef.current || !containerRef.current || !queryStr) return;
    const list = Array.from(containerRef.current.querySelectorAll("mark.hl"));
    setMatches(list);
    setMatchIndex(0);
    clearTimeout(scrollTimerRef.current);
    if (list.length > 0 && initialScrollDoneRef.current) {
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
    if (pendingScrollPageRef.current === pageNumber) {
      scrollToPageRef.current?.(pageNumber, "auto");
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

  useEffect(() => {
    if (numPages > 0 && pageHeight && initialPage > 1 && !initialScrollDoneRef.current) {
      scrollToPage(initialPage, "auto");
    }
  }, [numPages, pageHeight, initialPage, scrollToPage]);

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

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
        const { start, end } = rangeFromScroll(scrollY, window.innerHeight, slotHeight, numPages, scrollToolbarOffset);
        applyVisibleRange(start, end);
        const cur = Math.min(numPages, Math.max(1, Math.floor((scrollY + scrollToolbarOffset) / slotHeight) + 1));
        setCurrentPage((prev) => (prev === cur ? prev : cur));
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
    };
  }, [numPages, slotHeight, scrollToolbarOffset, applyVisibleRange]);

  const commitPageInput = () => {
    const parsed = parseInt(pageInput, 10);
    if (!Number.isFinite(parsed)) {
      setPageInput(String(currentPage));
      return;
    }
    goToPage(parsed);
  };

  const goToPage = (pageNumber) => {
    const clamped = Math.max(1, Math.min(pageNumber, numPages || 1));
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
          <div className="flex flex-wrap items-center gap-2 mt-2" data-testid="viewer-page-nav">
            <span className="text-sm text-muted2 hidden sm:inline">Pagina</span>
            <button
              type="button"
              onClick={() => goToPage(currentPage - 1)}
              disabled={currentPage <= 1}
              className="viewer-page-step"
              aria-label="Pagina precedente"
              data-testid="viewer-page-prev"
            >
              ←
            </button>
            <input
              type="number"
              min={1}
              max={numPages || 1}
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onBlur={commitPageInput}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitPageInput();
                }
              }}
              className="viewer-page-input"
              data-testid="viewer-page-input"
              aria-label="Numero pagina"
            />
            <button
              type="button"
              onClick={() => goToPage(currentPage + 1)}
              disabled={currentPage >= (numPages || 1)}
              className="viewer-page-step"
              aria-label="Pagina successiva"
              data-testid="viewer-page-next"
            >
              →
            </button>
            <span className="text-sm text-ink font-medium whitespace-nowrap">
              di <span data-testid="viewer-page-total">{numPages || "…"}</span>
            </span>
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
        <div className="viewer-search-bar sticky top-[57px] z-20" data-testid="viewer-search-bar">
          <span className="text-mono text-xs uppercase tracking-wide shrink-0">Cerca</span>
          <span className="font-medium truncate max-w-xs">"{queryStr}"</span>
          <span className="text-mono text-xs shrink-0" data-testid="match-counter">
            {matches.length === 0 ? "Nessun risultato" : `Risultato ${matchIndex + 1} di ${matches.length}`}
          </span>
          <div className="flex items-center gap-1 ml-auto shrink-0">
            <button onClick={goPrev} disabled={matches.length === 0} className="viewer-search-btn" title="Precedente" data-testid="match-prev">
              <ChevronUp size={14} />
            </button>
            <button onClick={goNext} disabled={matches.length === 0} className="viewer-search-btn" title="Successivo (n)" data-testid="match-next">
              <ChevronDown size={14} />
            </button>
            <button onClick={() => setHighlightsHidden((value) => !value)} className="viewer-search-btn" title="Mostra/Nascondi evidenziazione (Esc)" data-testid="toggle-highlights">
              {highlightsHidden ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button onClick={clearQuery} className="viewer-search-btn text-xs font-mono uppercase tracking-wider" title="Cancella ricerca" data-testid="clear-search">
              ×
            </button>
          </div>
        </div>
      )}

      <div ref={containerRef} className="flex-1 flex flex-col items-center py-8 px-2 md:px-4">
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
