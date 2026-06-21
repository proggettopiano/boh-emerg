import React from "react";
import { useNavigate } from "react-router-dom";
import { FileText } from "lucide-react";

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlight(text, q) {
  if (!text || !q) return text;
  try {
    const re = new RegExp(`(${escapeRegExp(q)})`, "ig");
    const parts = text.split(re);
    let offset = 0;
    return parts.map((part) => {
      const key = `${offset}-${part}`;
      offset += part.length;
      return part.toLowerCase() === q.toLowerCase()
        ? <mark key={key} className="hl">{part}</mark>
        : <span key={key}>{part}</span>;
    });
  } catch {
    return text;
  }
}

/**
 * Shared search results list — used by SharedLibraryDetail and SharedView.
 * Renders null while results are loading (null), empty state when [], list when populated.
 *
 * @param {Array|null} results
 * @param {string}     q           Raw user query (used for highlight and viewer URL)
 * @param {string}     shareToken  Passed as &share= to PdfViewer when set
 * @param {string}     emptyText   Context-specific empty message
 */
export default function SearchResults({ results, q, shareToken = "", emptyText = "Nessun risultato trovato." }) {
  const navigate = useNavigate();
  if (!results) return null;

  const handleClick = (r) => {
    const pageNum = r.viewer_page ?? r.actual_page ?? r.page;
    const pageParam = pageNum ? String(pageNum) : (r.page_label ?? "");
    navigate(
      `/viewer/${r.pdf_id}?page=${encodeURIComponent(pageParam)}&q=${encodeURIComponent(q.trim())}` +
      (shareToken ? `&share=${encodeURIComponent(shareToken)}` : "")
    );
  };

  return (
    <ul className="border-t border-rule">
      {results.length === 0 && (
        <li className="py-8 text-center text-muted2 text-sm italic">{emptyText}</li>
      )}
      {results.map((r, idx) => (
        <li key={`${r.pdf_id}-${r.page}-${idx}`} className="py-4 border-b border-rule hover:bg-canvas2 px-2 -mx-2 transition-colors">
          <button onClick={() => handleClick(r)} className="text-left w-full flex items-start gap-4">
            <FileText size={20} className="text-muted2 mt-1 shrink-0" />
            <div className="min-w-0">
              <div className="font-display font-bold text-lg hover:underline decoration-2 underline-offset-4">
                {highlight(r.title, q)}
                <span className="text-mono text-xs font-normal text-muted3 ml-2">PAG {r.page_label || r.page}</span>
                {r.is_protected && (
                  <span className="text-mono text-xs font-normal ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded-sm">PROTETTO</span>
                )}
              </div>
              {r.snippet && (
                <p className="text-sm text-muted2 mt-1 leading-relaxed">{highlight(r.snippet, q)}</p>
              )}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
