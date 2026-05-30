import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

export const TOOLBAR_OFFSET = 108;
export const TOOLBAR_OFFSET_WITH_SEARCH = 148;

const SCROLL_SYNC_DEBOUNCE_MS = 80;
const URL_SYNC_DEBOUNCE_MS = 350;

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Visible page from mounted DOM wrappers; falls back to slot estimate. */
export function detectVisiblePage(scrollY, getToolbarOffset, pageRefs, numPages, slotHeight) {
  if (numPages <= 0) return 1;
  const viewTop = scrollY + getToolbarOffset();
  const viewCenter = viewTop + window.innerHeight * 0.35;
  let bestFromDom = null;
  let anyMounted = false;

  for (let p = 1; p <= numPages; p += 1) {
    const el = pageRefs.current[p];
    if (!el) continue;
    anyMounted = true;
    const top = el.offsetTop;
    const bottom = top + el.offsetHeight;
    if (viewCenter >= top && viewCenter < bottom) return p;
    if (top <= viewCenter) bestFromDom = p;
  }

  if (anyMounted && bestFromDom != null) return bestFromDom;
  return Math.min(numPages, Math.max(1, Math.floor(viewTop / slotHeight) + 1));
}

function findFirstMatchIndexOnPage(matches, pageNum) {
  for (let i = 0; i < matches.length; i += 1) {
    const wrapper = matches[i].closest("[data-pdf-page]");
    if (wrapper && parseInt(wrapper.getAttribute("data-pdf-page"), 10) === pageNum) return i;
  }
  return -1;
}

function countMatchesOnPage(matches, pageNum) {
  let n = 0;
  for (let i = 0; i < matches.length; i += 1) {
    const wrapper = matches[i].closest("[data-pdf-page]");
    if (wrapper && parseInt(wrapper.getAttribute("data-pdf-page"), 10) === pageNum) n += 1;
  }
  return n;
}

function usePageController({
  numPages,
  slotHeight,
  getToolbarOffset,
  setRangeAround,
  pageRefs,
  mountedRef,
  pendingScrollPageRef,
  programmaticScrollRef,
  currentPageRef,
  onPageChange,
}) {
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");

  const setPageState = useCallback(
    (page, options = {}) => {
      const p = Math.max(1, Math.min(page, numPages || page || 1));
      currentPageRef.current = p;
      setCurrentPage(p);
      setPageInput(String(p));
      if (!options.skipNotify) onPageChange(p, options.source || "programmatic");
      return p;
    },
    [numPages, currentPageRef, onPageChange],
  );

  const scrollToPage = useCallback(
    (page, behavior = "auto") => {
      const p = Math.max(1, Math.min(page, numPages || page));
      programmaticScrollRef.current = true;
      pendingScrollPageRef.current = p;
      setRangeAround(p);
      setPageState(p, { source: "programmatic", skipNotify: false });

      const finish = () => {
        programmaticScrollRef.current = false;
        pendingScrollPageRef.current = null;
      };

      const tryScroll = (attempts = 0) => {
        if (!mountedRef.current) return;
        const el = pageRefs.current[p];
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - getToolbarOffset();
          window.scrollTo({ top: Math.max(0, top), behavior });
          finish();
          return;
        }
        if (attempts < 80) {
          requestAnimationFrame(() => tryScroll(attempts + 1));
          return;
        }
        window.scrollTo({ top: Math.max(0, (p - 1) * slotHeight), behavior });
        finish();
      };

      requestAnimationFrame(() => tryScroll());
    },
    [
      numPages,
      setRangeAround,
      slotHeight,
      getToolbarOffset,
      pageRefs,
      mountedRef,
      pendingScrollPageRef,
      programmaticScrollRef,
      setPageState,
    ],
  );

  const goToPage = useCallback(
    (pageNumber) => {
      scrollToPage(Math.max(1, Math.min(pageNumber, numPages || 1)), "smooth");
    },
    [numPages, scrollToPage],
  );

  const commitPageInput = useCallback(() => {
    const parsed = parseInt(pageInput, 10);
    if (!Number.isFinite(parsed)) {
      setPageInput(String(currentPage));
      return;
    }
    goToPage(parsed);
  }, [pageInput, currentPage, goToPage]);

  const applyPageFromScroll = useCallback(
    (scrollY) => {
      if (numPages <= 0 || programmaticScrollRef.current) return;
      const detected = detectVisiblePage(scrollY, getToolbarOffset, pageRefs, numPages, slotHeight);
      // #region agent log
      fetch('http://127.0.0.1:7258/ingest/5406e6ab-9fc7-4f89-bdc3-3e87909b21b9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'3ec2bc'},body:JSON.stringify({sessionId:'3ec2bc',location:'usePdfViewerState.js:applyPageFromScroll',message:'scroll detect',data:{detected,ref:currentPageRef.current,scrollY,prog:programmaticScrollRef.current},timestamp:Date.now(),hypothesisId:'B'})}).catch(()=>{});
      // #endregion
      if (detected !== currentPageRef.current) {
        setPageState(detected, { source: "scroll" });
      }
    },
    [numPages, getToolbarOffset, pageRefs, slotHeight, currentPageRef, programmaticScrollRef, setPageState],
  );

  return {
    currentPage,
    totalPages: numPages,
    pageInput,
    setPageInput,
    setPageState,
    goToPage,
    goToPrevPage: () => goToPage(currentPage - 1),
    goToNextPage: () => goToPage(currentPage + 1),
    commitPageInput,
    applyPageFromScroll,
    scrollToPage,
    canGoPrev: currentPage > 1,
    canGoNext: currentPage < (numPages || 1),
  };
}

function useSearchController({
  pdfId,
  initialQuery,
  containerRef,
  mountedRef,
  searchDriverDoneRef,
  getCurrentPage,
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState(initialQuery);
  const [highlightsVisible, setHighlightsVisible] = useState(true);
  const [matches, setMatches] = useState([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);

  const collectTimerRef = useRef(null);
  const matchesRef = useRef([]);

  const isSearchActive = query.length > 0;

  useEffect(() => {
    matchesRef.current = matches;
  }, [matches]);

  useEffect(() => {
    setQuery(initialQuery);
    setHighlightsVisible(true);
    setMatches([]);
    setCurrentMatchIndex(0);
    searchDriverDoneRef.current = false;
  }, [pdfId, initialQuery, searchDriverDoneRef]);

  const syncMatchIndexToPage = useCallback(
    (pageNum, list = matchesRef.current) => {
      if (!list.length) {
        setCurrentMatchIndex(0);
        return;
      }
      const idx = findFirstMatchIndexOnPage(list, pageNum);
      setCurrentMatchIndex(idx >= 0 ? idx : 0);
      list.forEach((node, i) => node.classList.toggle("hl-active", i === (idx >= 0 ? idx : 0)));
    },
    [],
  );

  const scrollToMatch = useCallback((list, idx, behavior = "smooth") => {
    const node = list[idx];
    if (!node || !mountedRef.current) return;
    list.forEach((item) => item.classList.remove("hl-active"));
    node.classList.add("hl-active");
    node.scrollIntoView({ behavior, block: "center" });
  }, [mountedRef]);

  const collectMatches = useCallback(() => {
    if (!mountedRef.current || !containerRef.current || !isSearchActive) return;
    const list = Array.from(containerRef.current.querySelectorAll("mark.hl"));
    matchesRef.current = list;
    setMatches(list);
    syncMatchIndexToPage(getCurrentPage(), list);
    // Search is observer-only after initial jump — never auto-scroll to match 0
  }, [isSearchActive, containerRef, mountedRef, syncMatchIndexToPage, getCurrentPage]);

  const scheduleCollect = useCallback(() => {
    if (!isSearchActive) return;
    clearTimeout(collectTimerRef.current);
    collectTimerRef.current = setTimeout(collectMatches, 120);
  }, [isSearchActive, collectMatches]);

  const onPageChanged = useCallback(
    (pageNum) => {
      if (!isSearchActive) return;
      // #region agent log
      fetch('http://127.0.0.1:7258/ingest/5406e6ab-9fc7-4f89-bdc3-3e87909b21b9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'3ec2bc'},body:JSON.stringify({sessionId:'3ec2bc',location:'usePdfViewerState.js:onPageChanged',message:'search observer',data:{pageNum,matchCount:matchesRef.current.length},timestamp:Date.now(),hypothesisId:'C'})}).catch(()=>{});
      // #endregion
      if (matchesRef.current.length > 0) {
        syncMatchIndexToPage(pageNum);
      } else {
        scheduleCollect();
      }
    },
    [isSearchActive, syncMatchIndexToPage, scheduleCollect],
  );

  const goToPrevMatch = useCallback(() => {
    if (matches.length === 0) return;
    const nextIndex = (currentMatchIndex - 1 + matches.length) % matches.length;
    setCurrentMatchIndex(nextIndex);
    scrollToMatch(matches, nextIndex);
  }, [matches, currentMatchIndex, scrollToMatch]);

  const goToNextMatch = useCallback(() => {
    if (matches.length === 0) return;
    const nextIndex = (currentMatchIndex + 1) % matches.length;
    setCurrentMatchIndex(nextIndex);
    scrollToMatch(matches, nextIndex);
  }, [matches, currentMatchIndex, scrollToMatch]);

  const toggleHighlights = useCallback(() => {
    setHighlightsVisible((v) => !v);
  }, []);

  const clearSearch = useCallback(() => {
    setQuery("");
    setMatches([]);
    matchesRef.current = [];
    setCurrentMatchIndex(0);
    setHighlightsVisible(true);
    navigate(`/viewer/${pdfId}?page=${getCurrentPage()}`, { replace: true });
  }, [pdfId, getCurrentPage, navigate]);

  const handleSearchEscape = useCallback(() => {
    if (!isSearchActive) return false;
    if (highlightsVisible) {
      setHighlightsVisible(false);
      return true;
    }
    clearSearch();
    return true;
  }, [isSearchActive, highlightsVisible, clearSearch]);

  const customTextRenderer = useCallback(
    ({ str }) => {
      if (!isSearchActive || !str) return str;
      try {
        const re = new RegExp(`(${escapeRegExp(query)})`, "ig");
        return str.replace(re, (m) => `<mark class="hl">${m}</mark>`);
      } catch (err) {
        console.error("[PdfViewer] Regex highlight failed:", { query, error: err.message });
        return str;
      }
    },
    [isSearchActive, query],
  );

  const pageNum = getCurrentPage();
  const onPageCount = countMatchesOnPage(matches, pageNum);
  const matchLabel = !matches.length
    ? "Nessun risultato"
    : onPageCount > 0
      ? `${currentMatchIndex + 1} / ${matches.length} · pag ${pageNum}`
      : `${currentMatchIndex + 1} / ${matches.length}`;

  return {
    query,
    isSearchActive,
    matches,
    currentMatchIndex,
    highlightsVisible,
    highlightsHidden: !highlightsVisible,
    matchLabel,
    goToPrevMatch,
    goToNextMatch,
    toggleHighlights,
    clearSearch,
    handleSearchEscape,
    customTextRenderer,
    scheduleCollect,
    collectMatches,
    onPageChanged,
    collectTimerRef,
  };
}

export function usePdfViewerState({
  pdfId,
  initialPage,
  initialQuery,
  numPages,
  slotHeight,
  getToolbarOffset,
  setRangeAround,
  containerRef,
  pageRefs,
  mountedRef,
  pendingScrollPageRef,
  initialScrollDoneRef,
}) {
  const navigate = useNavigate();
  const programmaticScrollRef = useRef(false);
  const searchDriverDoneRef = useRef(false);
  const initialJumpPendingRef = useRef(initialPage > 1);
  const currentPageRef = useRef(initialPage);
  const urlSyncTimerRef = useRef(null);
  const scrollSyncTimerRef = useRef(null);
  const activeQueryRef = useRef(initialQuery);

  const getCurrentPage = useCallback(() => currentPageRef.current, []);

  const syncUrl = useCallback(
    (page, q) => {
      clearTimeout(urlSyncTimerRef.current);
      urlSyncTimerRef.current = setTimeout(() => {
        const queryPart = q ? `&q=${encodeURIComponent(q)}` : "";
        navigate(`/viewer/${pdfId}?page=${page}${queryPart}`, { replace: true });
      }, URL_SYNC_DEBOUNCE_MS);
    },
    [pdfId, navigate],
  );

  const onPageChange = useCallback(
    (realPage, source = "scroll") => {
      const p = Math.max(1, Math.min(realPage, numPages || realPage || 1));
      // #region agent log
      fetch('http://127.0.0.1:7258/ingest/5406e6ab-9fc7-4f89-bdc3-3e87909b21b9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'3ec2bc'},body:JSON.stringify({sessionId:'3ec2bc',location:'usePdfViewerState.js:onPageChange',message:'page sync',data:{p,source,ref:currentPageRef.current},timestamp:Date.now(),hypothesisId:'A'})}).catch(()=>{});
      // #endregion

      onPageChangedRef.current?.(p);

      if (source !== "url") {
        syncUrl(p, activeQueryRef.current);
      }

      if (source === "programmatic" && initialPage > 1 && p === initialPage) {
        initialScrollDoneRef.current = true;
        searchDriverDoneRef.current = true;
      }
    },
    [numPages, syncUrl, initialPage, initialScrollDoneRef, searchDriverDoneRef],
  );

  const onPageChangedRef = useRef(null);

  const page = usePageController({
    numPages,
    slotHeight,
    getToolbarOffset,
    setRangeAround,
    pageRefs,
    mountedRef,
    pendingScrollPageRef,
    programmaticScrollRef,
    currentPageRef,
    onPageChange,
  });

  const search = useSearchController({
    pdfId,
    initialQuery,
    containerRef,
    mountedRef,
    searchDriverDoneRef,
    getCurrentPage,
  });

  activeQueryRef.current = search.query;
  onPageChangedRef.current = search.onPageChanged;

  useEffect(() => {
    const p = Math.max(1, Math.min(initialPage, numPages || initialPage || 1));
    currentPageRef.current = p;
    initialJumpPendingRef.current = initialPage > 1;
    searchDriverDoneRef.current = false;
    page.setPageState(p, { source: "document", skipNotify: true });
    return () => {
      clearTimeout(scrollSyncTimerRef.current);
      clearTimeout(urlSyncTimerRef.current);
    };
  }, [pdfId, initialPage, numPages]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => {
      clearTimeout(scrollSyncTimerRef.current);
      clearTimeout(urlSyncTimerRef.current);
    };
  }, []);

  const scrollToPageRef = useRef(null);
  scrollToPageRef.current = page.scrollToPage;

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (search.handleSearchEscape()) e.preventDefault();
        return;
      }
      if (document.activeElement?.tagName === "INPUT") return;
      if (!search.isSearchActive) return;
      if (e.key === "n") {
        e.preventDefault();
        search.goToNextMatch();
      } else if (e.key === "N") {
        e.preventDefault();
        search.goToPrevMatch();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [search]);

  const onPageRender = useCallback(
    (pageNumber, generation, renderGenerationRef) => {
      if (!mountedRef.current || generation !== renderGenerationRef.current) return;
      if (pendingScrollPageRef.current === pageNumber) {
        scrollToPageRef.current?.(pageNumber, "auto");
        return;
      }
      if (
        initialJumpPendingRef.current
        && !initialScrollDoneRef.current
        && pageNumber === currentPageRef.current
      ) {
        initialJumpPendingRef.current = false;
        initialScrollDoneRef.current = true;
        searchDriverDoneRef.current = true;
        if (search.isSearchActive) search.collectMatches();
      }
      search.scheduleCollect();
    },
    [mountedRef, pendingScrollPageRef, initialScrollDoneRef, searchDriverDoneRef, search],
  );

  const handleScroll = useCallback(
    (scrollY) => {
      clearTimeout(scrollSyncTimerRef.current);
      scrollSyncTimerRef.current = setTimeout(() => {
        page.applyPageFromScroll(scrollY);
      }, SCROLL_SYNC_DEBOUNCE_MS);
    },
    [page],
  );

  const completeInitialJump = useCallback(() => {
    initialScrollDoneRef.current = true;
    searchDriverDoneRef.current = true;
    if (search.isSearchActive) {
      search.collectMatches();
    }
  }, [initialScrollDoneRef, searchDriverDoneRef, search]);

  return {
    page,
    search,
    scrollToPageRef,
    onPageRender,
    handleScroll,
    completeInitialJump,
    currentPageRef,
  };
}

export default usePdfViewerState;
