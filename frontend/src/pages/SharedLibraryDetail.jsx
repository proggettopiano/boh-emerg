import React, { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Copy, FileText, Plus, Trash2, Search as SearchIcon } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";

export default function SharedLibraryDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [lib, setLib] = useState(null);
  const [allPdfs, setAllPdfs] = useState([]);
  const [showAdd, setShowAdd] = useState(false);
  const [q, setQ] = useState("");
  const [searchResults, setSearchResults] = useState(null);

  const load = async () => {
    try { const r = await api.get(`/libraries/${id}`); setLib(r.data); }
    catch (e) { toast.error("Libreria non accessibile"); navigate("/libraries"); }
  };
  useEffect(() => { load(); }, [id]); // eslint-disable-line

  const loadAll = async () => { const r = await api.get("/pdfs"); setAllPdfs(r.data.items); };
  const openAdd = () => { loadAll(); setShowAdd(true); };

  const addPdfs = async (ids) => {
    await api.post(`/libraries/${id}/pdfs`, { pdf_ids: ids });
    setShowAdd(false); load();
  };
  const removePdf = async (pdfId) => { await api.delete(`/libraries/${id}/pdfs/${pdfId}`); load(); };

  const copyLink = () => {
    const url = `${window.location.origin}/shared/${lib.share_token}`;
    navigator.clipboard.writeText(url); toast.success("Link copiato");
  };

  useEffect(() => {
    if (!q.trim()) { setSearchResults(null); return; }
    const t = setTimeout(async () => {
      try { const r = await api.get("/search", { params: { q, library_id: id } }); setSearchResults(r.data.results); }
      catch { setSearchResults([]); }
    }, 220);
    return () => clearTimeout(t);
  }, [q, id]);

  if (!lib) return <div className="p-12 text-mono text-sm text-muted2">Caricamento…</div>;

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12" data-testid="shared-library-detail">
      <button onClick={() => navigate("/libraries")} className="text-mono text-xs text-muted2 hover:text-ink mb-3">← TUTTE LE LIBRERIE</button>
      <div className="flex items-end justify-between flex-wrap gap-4 mb-8">
        <div>
          <p className="overline mb-2">LIBRERIA CONDIVISA</p>
          <h1 className="font-display font-black text-4xl tracking-tighter">{lib.name}</h1>
          {lib.description && <p className="text-[#525252] mt-2 max-w-2xl">{lib.description}</p>}
        </div>
        <div className="flex gap-2">
          <button onClick={copyLink} className="btn-ghost border border-rule rounded-sm px-4 py-2" data-testid="copy-share-link"><Copy size={14} /> Copia link</button>
          {lib.is_owner && <button onClick={openAdd} className="btn-primary" data-testid="add-pdfs-btn"><Plus size={14} /> Aggiungi PDF</button>}
        </div>
      </div>

      <div className="border-2 border-ink rounded-md mb-6" style={{ boxShadow: "0 4px 0 0 #0A0A0A" }}>
        <div className="flex items-center gap-3 px-4">
          <SearchIcon size={16} className="text-muted2" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cerca in questa libreria + nei tuoi PDF…" className="w-full py-3 outline-none bg-transparent" data-testid="library-search-input" />
        </div>
      </div>

      {searchResults && (
        <ul className="border-t border-rule mb-8" data-testid="library-search-results">
          {searchResults.length === 0 && <li className="py-6 text-center text-muted2 text-sm">Nessun risultato</li>}
          {searchResults.map((r) => (
            <li key={r.pdf_id + ":" + r.page} className="py-3 border-b border-rule">
              <button onClick={() => navigate(`/viewer/${r.pdf_id}?page=${r.page}&q=${encodeURIComponent(q)}`)} className="text-left w-full">
                <div className="font-display font-medium hover:underline">{r.title} <span className="text-mono text-xs text-muted2">PAG {r.page}</span></div>
                {r.snippet && <p className="text-sm text-[#525252] mt-1">{r.snippet}</p>}
              </button>
            </li>
          ))}
        </ul>
      )}

      <ul className="border-t border-rule">
        {(lib.pdfs || []).length === 0 && <li className="py-12 text-center text-muted2 text-sm" data-testid="library-no-pdfs">Nessun PDF in questa libreria.</li>}
        {(lib.pdfs || []).map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-4 py-3 border-b border-rule group hover:bg-canvas2 px-2 -mx-2 transition-colors">
            <button onClick={() => navigate(`/viewer/${p.id}`)} className="text-left flex-1 flex items-center gap-3 min-w-0">
              <FileText size={16} strokeWidth={1.5} />
              <div className="min-w-0">
                <div className="font-medium hover:underline truncate">{p.title}</div>
                <div className="text-mono text-xs text-muted2">{p.created_at?.slice(0, 10)} · {p.pages}pp</div>
              </div>
            </button>
            {lib.is_owner && <button onClick={() => removePdf(p.id)} className="btn-ghost opacity-0 group-hover:opacity-100"><Trash2 size={14} /></button>}
          </li>
        ))}
      </ul>

      {showAdd && (
        <AddPdfsModal allPdfs={allPdfs} existing={lib.pdf_ids} onClose={() => setShowAdd(false)} onAdd={addPdfs} />
      )}
    </div>
  );
}

function AddPdfsModal({ allPdfs, existing, onClose, onAdd }) {
  const [picked, setPicked] = useState([]);
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const candidates = allPdfs.filter((p) => !existing.includes(p.id));
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose} data-testid="add-pdfs-modal">
      <div className="bg-white border border-rule rounded-md w-full max-w-xl p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-display text-2xl font-bold mb-4">Aggiungi PDF</h2>
        {candidates.length === 0 ? (
          <p className="text-muted2 text-sm py-6">Tutti i tuoi PDF sono già in questa libreria.</p>
        ) : (
          <ul className="max-h-80 overflow-y-auto border-t border-rule">
            {candidates.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-2 border-b border-rule">
                <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} data-testid={`add-pick-${p.id}`} />
                <span className="flex-1 truncate text-sm">{p.title}</span>
                <span className="text-mono text-xs text-muted2">{p.pages}pp</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="btn-ghost border border-rule rounded-sm px-4 py-2">Annulla</button>
          <button disabled={picked.length === 0} onClick={() => onAdd(picked)} className="btn-primary disabled:opacity-40" data-testid="confirm-add-pdfs">Aggiungi {picked.length || ""}</button>
        </div>
      </div>
    </div>
  );
}
