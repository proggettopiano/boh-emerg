import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Trash2, FileText, Upload as UploadIcon, Star, Tag as TagIcon } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import UploadModal from "@/components/UploadModal";
import TagEditor from "@/components/TagEditor";

export default function Library() {
  const [items, setItems] = useState([]);
  const [tags, setTags] = useState([]);
  const [sort, setSort] = useState("date_desc");
  const [favOnly, setFavOnly] = useState(false);
  const [tagFilter, setTagFilter] = useState("");
  const [openUpload, setOpenUpload] = useState(false);
  const [editTagsFor, setEditTagsFor] = useState(null);
  const navigate = useNavigate();

  const load = async () => {
    try {
      const params = { sort };
      if (favOnly) params.favorite = true;
      if (tagFilter) params.tag = tagFilter;
      const r = await api.get("/pdfs", { params });
      setItems(r.data.items);
      setTags(r.data.tags || []);
    } catch {}
  };
  useEffect(() => { load(); }, [sort, favOnly, tagFilter]); // eslint-disable-line

  const remove = async (id, title) => {
    if (!window.confirm(`Eliminare definitivamente "${title}"?`)) return;
    try { await api.delete(`/pdfs/${id}`); toast.success("Eliminato"); load(); }
    catch (e) { toast.error("Errore"); }
  };

  const toggleFav = async (p) => {
    try { const r = await api.patch(`/pdfs/${p.id}`, { is_favorite: !p.is_favorite }); setItems((arr) => arr.map((x) => x.id === p.id ? r.data : x)); }
    catch { toast.error("Errore"); }
  };

  const updateItem = (updated) => setItems((arr) => arr.map((x) => x.id === updated.id ? updated : x));

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-10" data-testid="library-page">
        <div>
          <p className="overline mb-2">IL TUO ARCHIVIO</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Libreria</h1>
          <p className="text-mono text-sm text-muted2 mt-2"><span data-testid="library-count">{items.length}</span> PDF{favOnly ? " preferiti" : tagFilter ? ` con tag "${tagFilter}"` : " indicizzati"}</p>
        </div>
        <button onClick={() => setOpenUpload(true)} className="btn-primary" data-testid="library-upload-btn"><UploadIcon size={16} /> Carica PDF</button>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4 pb-4 border-b border-rule">
        <button
          onClick={() => setFavOnly((v) => !v)}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-sm border transition-colors ${favOnly ? "bg-ink text-white border-ink" : "border-rule hover:border-ink"}`}
          data-testid="filter-favorites"
        >
          <Star size={14} fill={favOnly ? "#FFFFFF" : "none"} /> Preferiti
        </button>
        {tags.length > 0 && (
          <select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} className="border border-rule rounded-sm px-3 py-1.5 text-sm bg-white" data-testid="filter-tag">
            <option value="">Tutti i tag</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="overline">ORDINA</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="border border-rule rounded-sm px-3 py-1.5 text-sm bg-white" data-testid="library-sort">
            <option value="date_desc">Più recenti</option>
            <option value="date_asc">Meno recenti</option>
            <option value="name_asc">Nome A→Z</option>
            <option value="name_desc">Nome Z→A</option>
          </select>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="border border-dashed border-rule rounded-md p-16 text-center" data-testid="library-empty">
          <FileText size={32} className="mx-auto mb-3 text-muted3" strokeWidth={1.5} />
          <h3 className="font-display text-xl font-bold mb-1">Nessun PDF</h3>
          <p className="text-muted2 mb-6">{favOnly || tagFilter ? "Modifica i filtri o carica nuovi PDF." : "Carica qualche PDF per iniziare a cercare."}</p>
          <button onClick={() => setOpenUpload(true)} className="btn-primary"><UploadIcon size={16} /> Carica PDF</button>
        </div>
      ) : (
        <ul className="border-t border-rule">
          {items.map((p) => (
            <li key={p.id} className="group flex items-center justify-between gap-4 py-4 border-b border-rule hover:bg-canvas2 px-2 -mx-2 transition-colors" data-testid={`library-item-${p.id}`}>
              <button onClick={() => toggleFav(p)} className="shrink-0" data-testid={`fav-btn-${p.id}`} title={p.is_favorite ? "Rimuovi preferito" : "Aggiungi ai preferiti"}>
                <Star size={18} strokeWidth={1.5} fill={p.is_favorite ? "#0A0A0A" : "none"} className={p.is_favorite ? "" : "text-muted3 hover:text-ink"} />
              </button>
              <button onClick={() => navigate(`/viewer/${p.id}`)} className="flex-1 text-left flex items-center gap-3 min-w-0">
                <FileText size={18} strokeWidth={1.5} className="shrink-0 text-muted2" />
                <div className="min-w-0">
                  <div className="font-display text-lg font-medium group-hover:underline decoration-2 underline-offset-4 truncate">{p.title}</div>
                  <div className="flex items-center gap-2 flex-wrap mt-0.5">
                    <span className="text-mono text-xs text-muted2">
                      {p.created_at?.slice(0, 10)} · {p.pages}pp · {(p.size / 1024).toFixed(0)} KB{p.used_ocr ? " · OCR" : ""}
                    </span>
                    <span className={`text-mono text-[10px] px-1.5 py-0.5 rounded-sm ${p.storage_type === "google_drive" ? "bg-ink text-white" : "bg-canvas3 text-muted2"}`} title={p.storage_type === "google_drive" ? `Drive · ${p.drive_file_id}` : `Locale · ${p.file_path}`}>
                      {p.storage_type === "google_drive" ? "☁ DRIVE" : "▣ LOCALE"}
                    </span>
                    {(p.tags || []).map((t) => (
                      <span key={t} className="text-mono text-[10px] px-1.5 py-0.5 bg-canvas3 rounded-sm">{t}</span>
                    ))}
                  </div>
                </div>
              </button>
              <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 shrink-0">
                <button onClick={() => setEditTagsFor(p)} className="btn-ghost" data-testid={`tags-btn-${p.id}`} title="Tag"><TagIcon size={15} /></button>
                <button onClick={() => remove(p.id, p.title)} className="btn-ghost text-red-600" data-testid={`library-delete-${p.id}`} title="Elimina"><Trash2 size={15} /></button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <UploadModal open={openUpload} onClose={() => setOpenUpload(false)} onComplete={load} />
      {editTagsFor && <TagEditor pdf={editTagsFor} onClose={() => setEditTagsFor(null)} onUpdate={updateItem} />}
    </div>
  );
}
