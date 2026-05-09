import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Shield, Cloud, HardDrive, Users, FileText, AlertTriangle, RefreshCw, ScrollText, FlaskConical, Unlink, HardDriveUpload } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startGoogleOAuth } from "@/lib/google";

export default function Admin() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [users, setUsers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [master, setMaster] = useState(null);

  const isAdmin = user?.email?.toLowerCase() === "admin@scorelib.app" || user?.is_admin;

  const load = async () => {
    setBusy(true);
    try {
      const [s, u, m] = await Promise.all([
        api.get("/admin/stats"),
        api.get("/admin/users"),
        api.get("/admin/master-drive/status"),
      ]);
      setStats(s.data); setUsers(u.data.users); setMaster(m.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Accesso negato");
      if (e.response?.status === 403) navigate("/");
    } finally { setBusy(false); }
  };

  const connectMaster = async () => { try { await startGoogleOAuth("master"); } catch { toast.error("Errore OAuth"); } };
  const disconnectMaster = async () => {
    if (!window.confirm("Scollegare il Master Drive? I backup futuri si fermeranno (per gli utenti che dipendono da questo).")) return;
    try { await api.post("/admin/master-drive/disconnect"); toast.success("Disconnesso"); load(); }
    catch (e) { toast.error("Errore"); }
  };
  const testMaster = async () => {
    setBusy(true);
    try { const r = await api.post("/admin/master-drive/test"); toast.success(`Test OK · ${r.data.files_in_root} file in root`); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Test fallito"); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (isAdmin) load(); }, [isAdmin]); // eslint-disable-line

  if (!isAdmin) {
    return (
      <div className="max-w-2xl mx-auto p-12 text-center" data-testid="admin-denied">
        <Shield size={32} className="mx-auto mb-3 text-muted2" strokeWidth={1.5} />
        <h2 className="font-display text-2xl font-bold mb-2">Accesso riservato</h2>
        <p className="text-muted2">Questa sezione è disponibile solo per l'amministratore.</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 py-12" data-testid="admin-page">
      <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
        <div>
          <p className="overline mb-2 flex items-center gap-2"><Shield size={12} /> AMMINISTRATORE</p>
          <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Pannello Admin</h1>
          <p className="text-mono text-sm text-muted2 mt-2">{user.email}</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate("/logs")} className="btn-ghost border border-rule rounded-sm px-3 py-2 text-sm" data-testid="admin-goto-logs">
            <ScrollText size={14} /> Vai ai log
          </button>
          <button onClick={load} disabled={busy} className="btn-primary disabled:opacity-50" data-testid="admin-refresh">
            <RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Aggiorna
          </button>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10" data-testid="admin-stats">
          <Stat icon={<Users size={16} />} label="Utenti totali" value={stats.users_total} />
          <Stat icon={<Cloud size={16} />} label="Utenti Google" value={stats.google_users} />
          <Stat icon={<HardDrive size={16} />} label="Utenti locali" value={stats.local_users} />
          <Stat icon={<FileText size={16} />} label="PDF totali" value={stats.pdfs_total} />
          <Stat icon={<Cloud size={16} />} label="PDF su Drive" value={stats.backed_up_pdfs} />
          <Stat icon={<Users size={16} />} label="Librerie condivise" value={stats.shared_libraries} />
          <Stat icon={<ScrollText size={16} />} label="Eventi 24h" value={stats.events_24h} />
          <Stat icon={<AlertTriangle size={16} className="text-red-500" />} label="Errori 24h" value={stats.errors_24h} accent={stats.errors_24h > 0} />
        </div>
      )}

      {/* Master Drive panel */}
      <div className="border border-rule rounded-md p-5 mb-10 bg-white" data-testid="master-drive-panel">
        <div className="flex items-start justify-between flex-wrap gap-4 mb-3">
          <div>
            <h2 className="font-display font-bold text-xl tracking-tight flex items-center gap-2"><Cloud size={18} /> Master Drive (backup di sistema)</h2>
            <p className="text-sm text-muted2 mt-1 max-w-2xl">Account Google master che riceve i backup di tutti gli utenti che hanno il backup attivo. Struttura: <span className="text-mono">/ScoreLib/&#123;user_id&#125;/&lt;file&gt;.pdf</span></p>
          </div>
          <div className="flex gap-2">
            {!master?.connected ? (
              <button onClick={connectMaster} className="btn-primary !py-2 !px-3 text-sm" data-testid="master-connect-btn"><HardDriveUpload size={14} /> Connetti master</button>
            ) : (
              <>
                <button onClick={testMaster} disabled={busy} className="btn-ghost border border-rule rounded-sm px-3 py-1.5 text-sm" data-testid="master-test-btn"><FlaskConical size={14} /> Testa</button>
                <button onClick={disconnectMaster} className="btn-ghost border border-red-300 text-red-600 hover:bg-red-50 rounded-sm px-3 py-1.5 text-sm" data-testid="master-disconnect-btn"><Unlink size={14} /> Disconnetti</button>
              </>
            )}
          </div>
        </div>
        {master?.connected && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 border-t border-rule pt-4">
            <div><div className="overline mb-1">Email master</div><div className="text-mono text-sm truncate" data-testid="master-email">{master.email}</div></div>
            <div><div className="overline mb-1">Folder root ID</div><div className="text-mono text-xs truncate" title={master.folder_root_id}>{master.folder_root_id}</div></div>
            <div><div className="overline mb-1">Stato</div><div className="text-emerald-700 text-sm font-medium">● Operativo</div></div>
          </div>
        )}
        {!master?.connected && (
          <div className="border-t border-rule pt-4 text-sm text-amber-700 bg-amber-50 rounded-sm p-3 border border-amber-200 mt-2">
            <AlertTriangle size={14} className="inline mr-1" /> Master Drive NON connesso. Solo gli utenti con il proprio Drive personale potranno attivare il backup.
          </div>
        )}
      </div>

      <div className="border border-rule rounded-md overflow-hidden">
        <div className="px-5 py-3 border-b border-rule flex items-center justify-between bg-canvas2">
          <h2 className="font-display font-bold tracking-tight">Utenti registrati</h2>
          <span className="text-mono text-xs text-muted2">{users.length} utenti</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="admin-users-table">
            <thead>
              <tr className="border-b border-rule text-left bg-canvas2/50">
                <th className="overline py-2 px-4">Email</th>
                <th className="overline py-2 px-4">Nome</th>
                <th className="overline py-2 px-4">Tipo</th>
                <th className="overline py-2 px-4">Storage</th>
                <th className="overline py-2 px-4 text-right">PDF</th>
                <th className="overline py-2 px-4 text-right">Su Drive</th>
                <th className="overline py-2 px-4">Backup</th>
                <th className="overline py-2 px-4">Iscritto</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} className="border-b border-rule hover:bg-canvas2" data-testid={`admin-user-${u.user_id}`}>
                  <td className="py-2 px-4">
                    <div className="flex items-center gap-2 min-w-0">
                      {u.is_admin && <Shield size={12} className="text-ink shrink-0" />}
                      <span className="text-mono text-xs truncate">{u.email}</span>
                    </div>
                  </td>
                  <td className="py-2 px-4 truncate max-w-[160px]">{u.name || <span className="text-muted3">—</span>}</td>
                  <td className="py-2 px-4">
                    <span className={`text-mono text-[10px] px-2 py-0.5 rounded-sm ${u.auth_provider === "google" ? "bg-ink text-white" : "bg-canvas3"}`}>
                      {u.auth_provider === "google" ? "GOOGLE" : "EMAIL"}
                    </span>
                  </td>
                  <td className="py-2 px-4">
                    <span className="text-mono text-[10px] inline-flex items-center gap-1">
                      {u.storage_type === "google_drive" ? <><Cloud size={11} /> DRIVE</> : <><HardDrive size={11} /> LOCALE</>}
                    </span>
                  </td>
                  <td className="py-2 px-4 text-right text-mono">{u.pdf_count}</td>
                  <td className="py-2 px-4 text-right text-mono">{u.backed_up_pdfs}</td>
                  <td className="py-2 px-4">
                    <span className={`text-mono text-[10px] ${u.backup_enabled ? "text-emerald-700" : "text-muted2"}`}>
                      {u.backup_enabled ? "ATTIVO" : "OFF"}
                    </span>
                  </td>
                  <td className="py-2 px-4 text-mono text-xs text-muted2">{u.created_at?.slice(0, 10)}</td>
                </tr>
              ))}
              {users.length === 0 && <tr><td colSpan="8" className="py-12 text-center text-muted2 text-sm">Nessun utente</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-10 border border-rule rounded-md p-5 bg-canvas2">
        <h3 className="font-display font-bold tracking-tight mb-3 flex items-center gap-2"><AlertTriangle size={16} /> Tipi di account</h3>
        <ul className="space-y-2 text-sm text-[#525252]">
          <li><strong className="text-ink">Utenti Google</strong> — login OAuth, file salvati su Google Drive (cloud persistente). Backup reale.</li>
          <li><strong className="text-ink">Utenti email/password</strong> — file salvati solo in locale sul server. Senza Drive, in caso di reset del server i dati vanno persi.</li>
          <li><strong className="text-ink">Persistenza</strong> — i file sono associati all'account in DB. Logout/login o cambio browser non li cancellano.</li>
        </ul>
      </div>
    </div>
  );
}

function Stat({ icon, label, value, accent }) {
  return (
    <div className={`border border-rule rounded-md p-4 ${accent ? "bg-red-50 border-red-300" : "bg-white"}`}>
      <div className="flex items-center gap-2 text-muted2 text-xs uppercase tracking-wider font-mono mb-2">
        {icon} {label}
      </div>
      <div className="font-display text-3xl font-bold tracking-tighter">{value}</div>
    </div>
  );
}
