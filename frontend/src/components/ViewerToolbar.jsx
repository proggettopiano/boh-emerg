import React from "react";
import {
  ArrowLeft,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Star,
  ChevronUp,
  ChevronDown,
  Eye,
  EyeOff,
  Cloud,
  HardDrive,
  Share2,
  Trash2,
} from "lucide-react";

/**
 * Presentational toolbar: page controls (left), search controls (right when active).
 */
export default function ViewerToolbar({
  meta,
  onBack,
  onToggleFavorite,
  scale,
  onZoomOut,
  onZoomIn,
  onZoomReset,
  page,
  search,
}) {
  return (
    <header className="viewer-toolbar" data-testid="viewer-toolbar">
      <div className="viewer-toolbar-header px-3 sm:px-4 md:px-6 py-2.5 flex items-center gap-2 sm:gap-3 flex-nowrap min-w-0">
        <button type="button" onClick={onBack} className="btn-ghost shrink-0" data-testid="viewer-back-btn">
          <ArrowLeft size={16} /> <span className="sr-only sm:not-sr-only sm:inline">Indietro</span>
        </button>
        <div className="flex-1 min-w-0 flex items-center justify-center gap-2 px-1 overflow-hidden">
          <span className="font-display font-semibold truncate text-center max-w-full">{meta?.title || "PDF"}</span>
          {meta && (
            <button type="button" onClick={onToggleFavorite} className="btn-ghost shrink-0" data-testid="viewer-favorite-btn" title="Preferito">
              <Star size={16} fill={meta.is_favorite ? "#0A0A0A" : "none"} strokeWidth={1.5} />
            </button>
          )}
          {meta?.storage_type && (
            <span
              className="text-mono text-[10px] px-2 py-0.5 rounded-sm border border-rule text-muted2 inline-flex items-center gap-1 shrink-0"
              data-testid="viewer-storage-badge"
              title={meta.storage_type === "google_drive" ? `Drive · ${meta.drive_file_id}` : `Locale · ${meta.file_path}`}
            >
              {meta.storage_type === "google_drive" ? <><Cloud size={10} /> DRIVE</> : <><HardDrive size={10} /> LOCALE</>}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button type="button" onClick={onZoomOut} className="btn-ghost" data-testid="viewer-zoom-out"><ZoomOut size={16} /></button>
          <span className="text-mono text-xs text-muted2 w-10 text-center">{Math.round(scale * 100)}%</span>
          <button type="button" onClick={onZoomIn} className="btn-ghost" data-testid="viewer-zoom-in"><ZoomIn size={16} /></button>
          <button type="button" onClick={onZoomReset} className="btn-ghost hidden sm:inline-flex" data-testid="viewer-zoom-reset" title="Reset zoom"><Maximize2 size={14} /></button>
        </div>
      </div>

      <div className={`viewer-toolbar-controls ${search.isSearchActive ? "viewer-toolbar-controls--search" : ""}`}>
        <div className="viewer-toolbar-group viewer-toolbar-group--page" data-testid="viewer-page-nav">
          <span className="viewer-toolbar-label">Pagina</span>
          <button
            type="button"
            onClick={page.goToPrevPage}
            disabled={!page.canGoPrev}
            className="viewer-page-step"
            aria-label="Pagina precedente"
            data-testid="viewer-page-prev"
          >
            ←
          </button>
          <input
            type="number"
            min={1}
            max={page.totalPages || 1}
            value={page.pageInput}
            onChange={(e) => page.setPageInput(e.target.value)}
            onBlur={page.commitPageInput}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                page.commitPageInput();
              }
            }}
            className="viewer-page-input"
            data-testid="viewer-page-input"
            aria-label="Numero pagina"
          />
          <button
            type="button"
            onClick={page.goToNextPage}
            disabled={!page.canGoNext}
            className="viewer-page-step"
            aria-label="Pagina successiva"
            data-testid="viewer-page-next"
          >
            →
          </button>
          <span className="viewer-toolbar-meta">
            di <span data-testid="viewer-page-total">{page.totalPages || "…"}</span>
          </span>
        </div>

        {search.isSearchActive && (
          <>
            <div className="viewer-toolbar-divider" aria-hidden />
            <div className="viewer-toolbar-group viewer-toolbar-group--search" data-testid="viewer-search-bar">
              <span className="viewer-toolbar-label">Cerca</span>
              <span className="viewer-search-query" title={search.query}>"{search.query}"</span>
              <span className="viewer-toolbar-meta" data-testid="match-counter">{search.matchLabel}</span>
              <div className="viewer-toolbar-actions">
                <button
                  type="button"
                  onClick={search.goToPrevMatch}
                  disabled={search.matches.length === 0}
                  className="viewer-tool-btn"
                  title="Risultato precedente"
                  data-testid="match-prev"
                >
                  <ChevronUp size={14} />
                </button>
                <button
                  type="button"
                  onClick={search.goToNextMatch}
                  disabled={search.matches.length === 0}
                  className="viewer-tool-btn"
                  title="Risultato successivo (n)"
                  data-testid="match-next"
                >
                  <ChevronDown size={14} />
                </button>
                <button
                  type="button"
                  onClick={search.toggleHighlights}
                  className="viewer-tool-btn"
                  title={search.highlightsVisible ? "Nascondi evidenziazione" : "Mostra evidenziazione"}
                  data-testid="toggle-highlights"
                >
                  {search.highlightsVisible ? <Eye size={14} /> : <EyeOff size={14} />}
                </button>
                <button
                  type="button"
                  onClick={search.dismissSearchPanel}
                  className="viewer-tool-btn viewer-tool-btn--clear"
                  title="Nascondi evidenziazione"
                  data-testid="dismiss-search"
                >
                  ×
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </header>
  );
}
