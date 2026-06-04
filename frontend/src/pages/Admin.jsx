import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Shield, Cloud, Users, FileText, AlertTriangle, RefreshCw, ScrollText, Unlink, HardDriveUpload, Check, X } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startGoogleOAuth } from "@/lib/google";

export default function Admin() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [requests, setRequests] = useState([]);
  const [busy, setBusy] = useState(false);
  const [master, setMaster] = useState(null);

  const isAdmin = user?.is_admin;

  const load = async () => {
    setBusy(true);
    try {
      const [s, m, r] = await Promise.all([
        api.get("/admin/stats"),
        api.get("/admin/master-drive/status"),
        api.get("/admin/access-requests"),
      ]);
      setStats(s.data); 
      setMaster(m.data);
      setRequests(r.data || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Accesso negato");
      if (e.response?.status === 403) navigate("/");
    } finally { setBusy(false); }
  };

  const handleRequest = async (email, action) => {
    try {
      await api.post(`/admin/access-requests/${action}`, { email });
      toast.success(`Richiesta ${action === "approve" ? "approvata" : "rifiutata"}`);
      load();
    } catch (e) {
      toast.error("Errore nell'elaborazione della richiesta");
    }
  };

  const connectMaster = async () => { try { await startGoogleOAuth("master"); } catch { toast.error("Errore OAuth"); } };
  const disconnectMaster = async () => {
    if (!window.confirm("Scollegare il Master Drive? Tutti i backup del gruppo si fermeranno.")) return;
    try { await api.post("/admin/master-drive/disconnect"); toast.success("Disconnesso"); load(); }
    catch (e) { toast.error("Errore"); }
  };
  
  useEffect(() => { if (isAdmin) load(); }, [isAdmin]); // eslint-disable-line

  if (!isAdmin) {
    return (
      <div className="max-w-2xl mx-auto p-12 text-center">
        <Shield size={32} className="mx-auto mb-3 text-muted2" strokeWidth={1.5} />
        <h2 className="font-display text-2xl font-bold mb-2">Accesso riservato</h2>
        <p className="text-muted2">Questa sezione è disponibile solo per l'amministratore.</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 py-12">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
        <div>
          <p className="overline mb-2 flex items-center gap-2"><Shield size={12} /> AMMINISTRATORE</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Gestione Gruppo</h1>
          <p className="text-mono text-sm text-muted2 mt-2">Chiesa Pomigliano · {user.email}</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate("/logs")} className="btn-ghost border border-rule rounded-sm px-3 py-2 text-sm">
            <ScrollText size={14} /> Log di sistema
          </button>
          <button onClick={load} disabled={busy} className="btn-primary">
            <RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Aggiorna
          </button>
        </div>
      </div>

      {/* Stats Quick Look */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
          <Stat icon={<Users size={16} />} label="Membri Gruppo" value={stats.users_total} />
          <Stat icon={<FileText size={16} />} label="Spartiti Totali" value={stats.pdfs_total} />
          <Stat icon={<Cloud size={16} />} label="Backup Drive" value={master?.connected ? "ATTIVO" : "OFF"} accent={!master?.connected} />
          <Stat icon={<AlertTriangle size={16} />} label="Richieste Pendenti" value={requests.filter(r => r.status === 'pending').length} accent={requests.filter(r => r.status === 'pending').length > 0} />
        </div>
      )}

      {/* Access Requests */}
      <div className="mb-10">
        <h2 className="font-display font-bold text-xl mb-4 flex items-center gap-2">
          <Users size={20} /> Richieste di Accesso
        </h2>
        <div className="border border-rule rounded-md overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-canvas2 border-b border-rule text-left">
              <tr>
                <th className="py-3 px-4 overline">Richiedente</th>
                <th className="py-3 px-4 overline">IP</th>
                <th className="py-3 px-4 overline">Stato</th>
                <th className="py-3 px-4 overline">Data / Ora</th>
                <th className="py-3 px-4 overline text-right">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r, idx) => (
                <tr key={idx} className="border-b border-rule hover:bg-canvas2">
                  <td className="py-3 px-4">
                    <div className="font-bold">{r.name}</div>
                    <div className="text-xs text-muted3 text-mono">{r.email}</div>
                  </td>
                  <td className="py-3 px-4 text-xs text-mono">{r.ip || "—"}</td>
                  <td className="py-3 px-4">
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                      r.status === 'approved' ? 'bg-emerald-100 text-emerald-700' : 
                      r.status === 'rejected' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
                    }`}>
                      {r.status.toUpperCase()}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-xs text-mono">
                    {r.created_at ? new Date(r.created_at).toLocaleString("it-IT") : "—"}
                  </td>
                  <td className="py-3 px-4 text-right">
                    {r.status === 'pending' && (
                      <div className="flex justify-end gap-2">
                        <button 
                          onClick={() => handleRequest(r.email, "approve")}
                          className="p-1.5 bg-emerald-100 text-emerald-700 rounded-sm hover:bg-emerald-200"
                          title="Approva"
                        >
                          <Check size={16} />
                        </button>
                        <button 
                          onClick={() => handleRequest(r.email, "reject")}
                          className="p-1.5 bg-red-100 text-red-700 rounded-sm hover:bg-red-200"
                          title="Rifiuta"
                        >
                          <X size={16} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {requests.length === 0 && (
                <tr>
                  <td colSpan="5" className="py-8 text-center text-muted3 italic">Nessuna richiesta trovata</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Master Drive panel */}
      <div className="border border-rule rounded-md p-5 mb-10 bg-white">
        <div className="flex items-start justify-between flex-wrap gap-4 mb-3">
          <div>
            <h2 className="font-display font-bold text-xl tracking-tight flex items-center gap-2"><Cloud size={18} /> Google Drive Master</h2>
            <p className="text-sm text-muted2 mt-1 max-w-2xl">
              Tutti gli spartiti caricati dal gruppo vengono salvati automaticamente su questo account Drive.
            </p>
          </div>
          <div className="flex gap-2">
            {!master?.connected ? (
              <button onClick={connectMaster} className="btn-primary !py-2 !px-3 text-sm">
                <HardDriveUpload size={14} /> Collega Account Google
              </button>
            ) : (
              <button onClick={disconnectMaster} className="btn-ghost border border-red-300 text-red-600 hover:bg-red-50 rounded-sm px-3 py-1.5 text-sm">
                <Unlink size={14} /> Scollega Drive
              </button>
            )}
          </div>
        </div>
        {master?.connected && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-rule pt-4">
            <div><div className="overline mb-1">Email collegata</div><div className="text-mono text-sm">{master.email}</div></div>
            <div><div className="overline mb-1">Stato Backup</div><div className="text-emerald-700 text-sm font-medium">● Sincronizzazione Attiva</div></div>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ icon, label, value, accent }) {
  return (
    <div className={`border border-rule rounded-md p-4 ${accent ? "bg-amber-50 border-amber-300" : "bg-white"}`}>
      <div className="flex items-center gap-2 text-muted2 text-xs uppercase tracking-wider font-mono mb-2">
        {icon} {label}
      </div>
      <div className="font-display text-3xl font-bold tracking-tighter">{value}</div>
    </div>
  );
}
