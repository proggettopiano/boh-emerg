import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Users, Trash2 } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";

export default function SharedLibraries() {
  const [items, setItems] = useState([]);
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const load = async () => { try { const r = await api.get("/libraries"); setItems(r.data.items); } catch {} };
  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    try { await api.post("/libraries", { name, description: desc }); setShow(false); setName(""); setDesc(""); toast.success("Libreria creata"); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };
  const del = async (id) => { if (!window.confirm("Eliminare la libreria condivisa?")) return; await api.delete(`/libraries/${id}`); load(); };

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-10" data-testid="shared-libraries-page">
        <div>
          <p className="overline mb-2">COLLABORAZIONE</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Librerie condivise</h1>
          <p className="text-[#525252] mt-2 max-w-xl">Raccogli spartiti e condividi un link con altri musicisti.</p>
        </div>
        <button onClick={() => setShow(true)} className="btn-primary" data-testid="new-library-btn"><Plus size={16} /> Nuova libreria</button>
      </div>

      {items.length === 0 ? (
        <div className="border border-dashed border-rule rounded-md p-16 text-center" data-testid="shared-empty">
          <Users size={32} className="mx-auto mb-3 text-muted3" strokeWidth={1.5} />
          <h3 className="font-display text-xl font-bold mb-1">Nessuna libreria</h3>
          <p className="text-muted2">Crea una libreria per raccogliere e condividere spartiti.</p>
        </div>
      ) : (
        <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="shared-libraries-list">
          {items.map((l) => (
            <li key={l.id} className="border border-rule rounded-md p-5 hover:border-ink transition-colors" data-testid={`shared-lib-${l.id}`}>
              <div className="flex items-start justify-between mb-3">
                <Link to={`/libraries/${l.id}`} className="block">
                  <h3 className="font-display text-xl font-bold tracking-tight hover:underline">{l.name}</h3>
                  {l.description && <p className="text-sm text-muted2 mt-1 line-clamp-2">{l.description}</p>}
                </Link>
                <button onClick={() => del(l.id)} className="btn-ghost" data-testid={`shared-lib-delete-${l.id}`}><Trash2 size={14} /></button>
              </div>
              <div className="flex items-center justify-between text-mono text-xs text-muted2 mt-3 pt-3 border-t border-rule">
                <span>{(l.pdf_ids || []).length} PDF</span>
                <span>{(l.members || []).length + 1} membri</span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {show && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setShow(false)} data-testid="new-library-modal">
          <form onClick={(e) => e.stopPropagation()} onSubmit={create} className="bg-white border border-rule rounded-md w-full max-w-md p-6 space-y-4">
            <h2 className="font-display text-2xl font-bold tracking-tight">Nuova libreria</h2>
            <div>
              <label className="overline block mb-2">Nome</label>
              <input required value={name} onChange={(e) => setName(e.target.value)} className="input-base" data-testid="new-library-name" />
            </div>
            <div>
              <label className="overline block mb-2">Descrizione</label>
              <textarea value={desc} onChange={(e) => setDesc(e.target.value)} className="input-base min-h-[80px]" data-testid="new-library-desc" />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShow(false)} className="btn-ghost border border-rule rounded-sm px-4 py-2">Annulla</button>
              <button type="submit" className="btn-primary" data-testid="new-library-submit">Crea</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
