import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Copy, FileText, Plus, Trash2, Search as SearchIcon, ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function SharedLibraryDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [lib, setLib] = useState(null);
  const [allPdfs, setAllPdfs] = useState([]);
  const [showAdd, setShowAdd] = useState(false);
  const [q, setQ] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const mountedRef = useRef(false);
  const isAdmin = user?.is_admin;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const load = async () => {
    try {
      const r = await api.get(`/libraries/${id}`);
      if (mountedRef.current) setLib(r.data);
    } catch (e) {
      if (mountedRef.current) {
        toast.error("Libreria non accessibile");
        navigate("/shared");
      }
    }
  };

  useEffect(() => { load(); }, [id]); // eslint-disable-line

  const loadAll = async () => {
    const r = await api.get("/pdfs");
    if (mountedRef.current) setAllPdfs(r.data.items);
  };
  const openAdd = () => { loadAll(); setShowAdd(true); };

  const addPdfs = async (ids) => {
    try {
      await api.post(`/libraries/${id}/pdfs`, { pdf_ids: ids });
      setShowAdd(false);
      toast.success("PDF aggiunti");
      load();
    } catch (e) {
      toast.error("Errore aggiunta PDF");
    }
  };
  
  const removePdf = async (pdfId) => {
    if (!window.confirm("Rimuovere questo spartito dalla libreria?")) return;
    try {
      await api.delete(`/libraries/${id}/pdfs/${pdfId}`);
      toast.success("Rimosso");
      load();
    } catch (e) {
      toast.error("Errore rimozione");
    }
  };

  const copyLink = () => {
    const url = `${window.location.origin}/shared/${lib.share_token}`;
    navigator.clipboard.writeText(url);
    toast.success("Link pubblico copiato");
  };

  useEffect(() => {
    if (!q.trim()) { setSearchResults(null); return undefined; }
    const ctrl = new AbortController();
    let alive = true;
    const timer = setTimeout(async () => {
      try {
        const r = await api.get("/search", { params: { q }, signal: ctrl.signal });
        // Filtra solo i risultati che appartengono alla libreria corrente
        const libPdfIds = new Set(lib.pdf_ids || []);
        const filtered = (r.data.results || []).filter(res => libPdfIds.has(res.pdf_id));
        if (alive && mountedRef.current) setSearchResults(filtered);
      } catch (e) {
        if (alive && mountedRef.current && e.name !== "CanceledError" && e.name !== "AbortError" && e.code !== "ERR_CANCELED") setSearchResults([]);
      }
    }, 350);
    return () => {
      alive = false;
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [q, id, lib?.pdf_ids]);

  if (!lib) return <div className="p-12 text-mono text-sm text-muted2">Caricamento...</div>;

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12">
      <button onClick={() => navigate("/shared")} className="flex items-center gap-2 text-mono text-xs text-muted2 hover:text-ink mb-6 uppercase tracking-widest">
        <ArrowLeft size={14} /> Torna alle librerie
      </button>
      
      <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
        <div>
          <p className="overline mb-2">GESTIONE LIBRERIA</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">{lib.name}</h1>
          {lib.description && <p className="text-muted2 mt-2 max-w-2xl">{lib.description}</p>}
        </div>
        <div className="flex gap-2">
          <button onClick={copyLink} className="btn-ghost border border-rule rounded-sm px-4 py-2 text-sm font-bold flex items-center gap-2">
            <Copy size={14} /> Copia Link Pubblico
          </button>
          {isAdmin && (
            <button onClick={openAdd} className="btn-primary flex items-center gap-2">
              <Plus size={16} /> Aggiungi PDF
            </button>
          )}
        </div>
      </div>

      <div className="relative mb-8">
        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none">
          <SearchIcon size={18} className="text-muted3" />
        </div>
        <input 
          value={q} 
          onChange={(e) => setQ(e.target.value)} 
          placeholder="Cerca spartiti in questa libreria..." 
          className="w-full pl-12 pr-4 py-4 bg-card border border-rule rounded-md focus:outline-none focus:border-ink transition-colors shadow-sm"
        />
      </div>

      {searchResults && (
        <div className="mb-10">
          <p className="overline mb-4">RISULTATI RICERCA</p>
          <ul className="border-t border-rule">
            {searchResults.length === 0 && <li className="py-8 text-center text-muted2 text-sm italic">Nessun risultato trovato in questa libreria.</li>}
            {searchResults.map((r, idx) => (
              <li key={idx} className="py-4 border-b border-rule hover:bg-canvas2 px-2 -mx-2 transition-colors">
                <button onClick={() => navigate(`/viewer/${r.pdf_id}?page=${r.page}&q=${encodeURIComponent(q)}`)} className="text-left w-full flex items-start gap-4">
                  <FileText size={20} className="text-muted2 mt-1 shrink-0" />
                  <div className="min-w-0">
                    <div className="font-display font-bold text-lg hover:underline decoration-2 underline-offset-4">{r.title} <span className="text-mono text-xs font-normal text-muted3 ml-2">PAGINA {r.page}</span></div>
                    {r.snippet && <p className="text-sm text-muted2 mt-1 leading-relaxed" dangerouslySetInnerHTML={{ __html: r.snippet.replace(new RegExp(q, 'gi'), match => `<mark class="bg-amber-100 text-amber-900 px-0.5 rounded-sm">${match}</mark>`) }} /> }
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="overline mb-4">CONTENUTO LIBRERIA ({(lib.pdfs || []).length})</p>
      <ul className="border-t border-rule">
        {(lib.pdfs || []).length === 0 && <li className="py-12 text-center text-muted2 text-sm italic">Questa libreria è vuota.</li>}
        {(lib.pdfs || []).map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-4 py-4 border-b border-rule group hover:bg-canvas2 px-2 -mx-2 transition-colors">
            <button onClick={() => navigate(`/viewer/${p.id}`)} className="text-left flex-1 flex items-center gap-4 min-w-0">
              <FileText size={20} strokeWidth={1.5} className="text-muted2 shrink-0" />
              <div className="min-w-0">
                <div className="font-display font-bold text-lg group-hover:underline decoration-2 underline-offset-4 truncate">{p.title}</div>
                <div className="text-mono text-xs text-muted3 mt-0.5">{p.created_at?.slice(0, 10)} — {p.pages} pagine — {(p.size / 1024).toFixed(0)} KB</div>
              </div>
            </button>
            {isAdmin && (
              <button 
                onClick={() => removePdf(p.id)} 
                className="btn-ghost text-muted3 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-all"
                title="Rimuovi dalla libreria"
              >
                <Trash2 size={16} />
              </button>
            )}
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
  const toggle = (pdfId) => setPicked((current) => (current.includes(pdfId) ? current.filter((id) => id !== pdfId) : [...current, pdfId]));
  const candidates = allPdfs.filter((pdf) => !existing.includes(pdf.id));
  
  return (
    <div className="fixed inset-0 z-50 bg-overlay flex items-center justify-center p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-card border border-rule rounded-md w-full max-w-xl shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="p-6 border-b border-rule flex items-center justify-between">
          <h2 className="font-display text-2xl font-black tracking-tight uppercase">Aggiungi Spartiti</h2>
          <button onClick={onClose} className="text-muted3 hover:text-ink">Chiudi</button>
        </div>
        <div className="p-0 max-h-[60vh] overflow-y-auto">
          {candidates.length === 0 ? (
            <p className="text-muted2 text-center py-12 italic">Tutti gli spartiti sono già in questa libreria.</p>
          ) : (
            <ul className="">
              {candidates.map((pdf) => (
                <li key={pdf.id} className="flex items-center gap-4 px-6 py-3 border-b border-rule hover:bg-canvas2 transition-colors cursor-pointer" onClick={() => toggle(pdf.id)}>
                  <input 
                    type="checkbox" 
                    checked={picked.includes(pdf.id)} 
                    onChange={() => {}} // Gestito dal click sulla riga
                    className="w-5 h-5 rounded border-rule text-ink focus:ring-ink"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-bold truncate">{pdf.title}</div>
                    <div className="text-mono text-[10px] text-muted3 uppercase">{pdf.pages} pagine — {pdf.created_at?.slice(0, 10)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="p-6 bg-canvas2 flex justify-end gap-3 rounded-b-md">
          <button onClick={onClose} className="px-4 py-2 text-sm font-bold hover:underline">Annulla</button>
          <button 
            disabled={picked.length === 0} 
            onClick={() => onAdd(picked)} 
            className="btn-primary disabled:opacity-30 disabled:cursor-not-allowed px-6"
          >
            Aggiungi {picked.length > 0 ? `(${picked.length})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
