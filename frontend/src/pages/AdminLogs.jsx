import React, { useState } from "react";
import { Lock, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";

export default function AdminLogs() {
  const [pwd, setPwd] = useState("");
  const [authed, setAuthed] = useState(false);
  const [items, setItems] = useState([]);
  const [types, setTypes] = useState([]);
  const [filterType, setFilterType] = useState("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("date_desc");
  const [busy, setBusy] = useState(false);

  const fetchLogs = async (password = pwd) => {
    setBusy(true);
    try {
      const r = await api.post("/admin/logs", { password }, { params: { event_type: filterType, q, sort, limit: 500 } });
      setItems(r.data.items); setTypes(r.data.types); setAuthed(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Errore"); setAuthed(false);
    } finally { setBusy(false); }
  };

  if (!authed) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-canvas">
        <form onSubmit={(e) => { e.preventDefault(); fetchLogs(pwd); }} className="w-full max-w-sm" data-testid="admin-login">
          <Lock size={28} strokeWidth={1.5} className="mb-3" />
          <h1 className="font-display font-black text-3xl tracking-tighter mb-2">Log del sito</h1>
          <p className="text-[#525252] mb-6 text-sm">Accesso protetto. Inserisci la password admin.</p>
          <input type="password" required value={pwd} onChange={(e) => setPwd(e.target.value)} placeholder="Password admin" className="input-base mb-3" data-testid="admin-pwd-input" />
          <button type="submit" disabled={busy} className="btn-primary w-full disabled:opacity-50" data-testid="admin-pwd-submit">{busy ? "Verifica…" : "Accedi"}</button>
        </form>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 py-12" data-testid="admin-logs-page">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-8">
        <div>
          <p className="overline mb-2">AMMINISTRAZIONE</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Log del sito</h1>
          <p className="text-mono text-sm text-muted2 mt-1">{items.length} eventi</p>
        </div>
        <button onClick={() => fetchLogs()} className="btn-primary" data-testid="admin-refresh-btn"><RefreshCw size={14} /> Aggiorna</button>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4 border border-rule rounded-sm p-3 bg-canvas2">
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <Search size={14} className="text-muted2" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cerca nei log…" className="bg-transparent outline-none text-sm w-full" data-testid="admin-search-input" />
        </div>
        <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="border border-rule rounded-sm px-2 py-1.5 text-sm bg-white" data-testid="admin-filter-type">
          <option value="all">Tutti gli eventi</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="border border-rule rounded-sm px-2 py-1.5 text-sm bg-white" data-testid="admin-sort">
          <option value="date_desc">Più recenti</option>
          <option value="date_asc">Meno recenti</option>
        </select>
        <button onClick={() => fetchLogs()} className="btn-ghost border border-rule rounded-sm px-3 py-1.5 text-sm" data-testid="admin-apply-btn">Applica</button>
      </div>

      <div className="overflow-x-auto border-t border-rule">
        <table className="w-full text-sm" data-testid="admin-logs-table">
          <thead>
            <tr className="border-b border-rule text-left">
              <th className="overline py-2 pr-3">Quando</th>
              <th className="overline py-2 pr-3">Evento</th>
              <th className="overline py-2 pr-3">Utente</th>
              <th className="overline py-2 pr-3">Descrizione</th>
              <th className="overline py-2 pr-3">Liv.</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && <tr><td colSpan="5" className="py-12 text-center text-muted2 text-sm">Nessun log</td></tr>}
            {items.map((l) => (
              <tr key={l.id} className="border-b border-rule hover:bg-canvas2">
                <td className="py-2 pr-3 text-mono text-xs text-muted2 whitespace-nowrap">{l.created_at?.slice(0, 19).replace("T", " ")}</td>
                <td className="py-2 pr-3 text-mono text-xs">{l.event_type}</td>
                <td className="py-2 pr-3 text-mono text-xs text-muted2">{l.user_id || "—"}</td>
                <td className="py-2 pr-3">{l.description}</td>
                <td className="py-2 pr-3 text-xs">
                  <span className={`px-2 py-0.5 rounded-sm border ${l.level === "error" ? "border-red-500 text-red-600" : l.level === "warn" ? "border-amber-500 text-amber-700" : "border-rule text-muted2"}`}>
                    {l.level || "info"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
