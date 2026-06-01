import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, Trash2, HardDriveUpload, RefreshCw, CheckCircle2, CloudOff, FlaskConical } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startGoogleOAuth } from "@/lib/google";

function Section({ title, children, testId }) {
  return (
    <section className="border-t border-rule py-8" data-testid={testId}>
      <h2 className="overline mb-4">{title}</h2>
      {children}
    </section>
  );
}

export default function Settings() {
  const { user, setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState(user?.name || "");
  const [howFound, setHowFound] = useState(user?.how_found || "");
  const [picture, setPicture] = useState(user?.picture || "");

  const [newEmail, setNewEmail] = useState("");
  const [emailPwd, setEmailPwd] = useState("");

  const [curPwd, setCurPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");

  const [bk, setBk] = useState(null);
  const [bkBusy, setBkBusy] = useState(false);
  const [connectBusy, setConnectBusy] = useState(false);

  const loadBackup = async () => {
    try { const r = await api.get("/backup/status"); setBk(r.data); } catch {}
  };
  useEffect(() => { loadBackup(); }, []);

  const saveProfile = async () => {
    try { const r = await api.patch("/profile", { name, how_found: howFound, picture }); setUser(r.data); toast.success("Profilo aggiornato"); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };
  const onPickFile = (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    if (f.size > 2 * 1024 * 1024) { toast.error("Max 2MB"); return; }
    const reader = new FileReader(); reader.onload = () => setPicture(reader.result); reader.readAsDataURL(f);
  };
  const changeEmail = async () => {
    try { const r = await api.post("/settings/email", { password: emailPwd, new_email: newEmail }); setUser(r.data); setNewEmail(""); setEmailPwd(""); toast.success("Email aggiornata"); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };
  const changePassword = async () => {
    try { await api.post("/settings/password", { current_password: curPwd, new_password: newPwd }); setCurPwd(""); setNewPwd(""); toast.success("Password aggiornata"); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };
  const toggleBackup = async () => {
    if (!user.backup_enabled && !bk?.drive_connected) { toast.error("Connetti Google Drive o chiedi all'admin di collegare il Master Drive"); return; }
    try { const r = await api.post("/settings/backup", { enabled: !user.backup_enabled }); setUser(r.data); loadBackup(); toast.success(`Backup ${r.data.backup_enabled ? "attivato" : "disattivato"}`); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };
  const connectDrive = async () => {
    if (connectBusy) return;
    setConnectBusy(true);
    try {
      await startGoogleOAuth("connect");
    } catch {
      toast.error("Errore Google OAuth");
    } finally {
      setConnectBusy(false);
    }
  };
  const runBackup = async () => {
    setBkBusy(true);
    try { const r = await api.post("/backup/run"); toast.success(`Backup completato · ${r.data.uploaded} caricati${r.data.errors ? `, ${r.data.errors} errori` : ""}`); loadBackup(); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
    finally { setBkBusy(false); }
  };
  const testBackup = async () => {
    setBkBusy(true);
    try { const r = await api.post("/backup/test"); toast.success(`Test OK · ${r.data.files_count} file nella cartella Drive`); loadBackup(); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore test"); }
    finally { setBkBusy(false); }
  };
  const deleteAccount = async () => {
    if (!window.confirm("Cancellare definitivamente l'account e tutti i tuoi PDF? Operazione irreversibile.")) return;
    if (!window.confirm("Sei davvero sicuro? Non puoi tornare indietro.")) return;
    try { await api.delete("/settings/account"); logout(); navigate("/register"); toast.success("Account eliminato"); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };

  if (!user) return null;

  return (
    <div className="max-w-3xl mx-auto px-6 md:px-12 py-12" data-testid="settings-page">
      <p className="overline mb-2">ACCOUNT</p>
      <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter mb-10">Impostazioni</h1>

      <Section title="PROFILO" testId="settings-profile-section">
        <div className="flex items-center gap-5 mb-4">
          <div className="w-20 h-20 rounded-md bg-canvas3 overflow-hidden border border-rule flex items-center justify-center">
            {picture ? <img src={picture} alt="profile" className="w-full h-full object-cover" /> : <span className="text-mono text-xs text-muted2">FOTO</span>}
          </div>
          <label className="btn-ghost border border-rule rounded-sm cursor-pointer">
            <input type="file" accept="image/*" className="hidden" onChange={onPickFile} data-testid="settings-picture-input" />
            Cambia foto
          </label>
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mb-4">
          <div><label className="overline block mb-2">Nome</label><input value={name} onChange={(e) => setName(e.target.value)} className="input-base" data-testid="settings-name-input" /></div>
          <div><label className="overline block mb-2">Come ci hai trovato</label><input value={howFound} onChange={(e) => setHowFound(e.target.value)} className="input-base" data-testid="settings-howfound-input" /></div>
        </div>
        <button onClick={saveProfile} className="btn-primary" data-testid="settings-save-profile">Salva profilo</button>
      </Section>

      <Section title="EMAIL" testId="settings-email-section">
        <p className="text-mono text-sm text-muted2 mb-3">Attuale: {user.email}</p>
        <div className="grid sm:grid-cols-2 gap-4 mb-3">
          <input type="email" placeholder="Nuova email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} className="input-base" data-testid="settings-new-email" />
          {user.auth_provider === "password" && (
            <input type="password" placeholder="Password attuale" value={emailPwd} onChange={(e) => setEmailPwd(e.target.value)} className="input-base" data-testid="settings-email-pwd" />
          )}
        </div>
        <button onClick={changeEmail} disabled={!newEmail} className="btn-primary disabled:opacity-40" data-testid="settings-change-email-btn">Cambia email</button>
      </Section>

      <Section title="PASSWORD" testId="settings-password-section">
        {user.auth_provider !== "password" && (
          <p className="text-sm text-muted2 mb-3">Hai effettuato l'accesso con Google. Imposta una password per usare anche il login email.</p>
        )}
        <div className="grid sm:grid-cols-2 gap-4 mb-3">
          {user.auth_provider === "password" && (
            <input type="password" placeholder="Password attuale" value={curPwd} onChange={(e) => setCurPwd(e.target.value)} className="input-base" data-testid="settings-cur-pwd" />
          )}
          <input type="password" placeholder="Nuova password (min 6)" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} className="input-base" data-testid="settings-new-pwd" />
        </div>
        <button onClick={changePassword} disabled={newPwd.length < 6} className="btn-primary disabled:opacity-40" data-testid="settings-change-pwd-btn">Aggiorna password</button>
      </Section>

      <Section title="BACKUP · GOOGLE DRIVE" testId="settings-backup-section">
        <div className="border border-rule rounded-md p-5 space-y-4">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <p className="font-medium flex items-center gap-2">
                {bk?.drive_connected ? <><CheckCircle2 size={16} className="text-emerald-600" /> {bk?.master_drive_connected && !bk?.user_drive_connected ? "Master Drive connesso" : "Drive connesso"}</> : <><CloudOff size={16} className="text-muted2" /> Drive non connesso</>}
              </p>
              {bk?.drive_email && <p className="text-mono text-xs text-muted2 mt-1">{bk.drive_email}</p>}
            </div>
            {!bk?.drive_connected && (
              <button onClick={connectDrive} disabled={connectBusy} className="btn-primary !py-2 !px-4 text-sm disabled:cursor-not-allowed disabled:opacity-50" data-testid="connect-drive-btn"><HardDriveUpload size={14} /> Connetti Google Drive</button>
            )}
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 border-t border-rule pt-4">
            <Stat label="Backup" value={user.backup_enabled ? "ON" : "OFF"} mono />
            <Stat label="File totali" value={bk?.total_pdfs ?? "—"} />
            <Stat label="Su Drive" value={bk?.backed_up_pdfs ?? "—"} />
            <Stat label="In attesa" value={bk?.pending_pdfs ?? "—"} />
          </div>

          {bk?.last_backup_at && <p className="text-mono text-xs text-muted2">Ultimo backup: {new Date(bk.last_backup_at).toLocaleString("it-IT")}</p>}

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-rule">
            <button onClick={toggleBackup} className={`px-4 py-2 rounded-sm font-medium text-sm ${user.backup_enabled ? "bg-emerald-500 text-white" : "bg-canvas3 text-ink border border-rule"}`} data-testid="settings-backup-toggle">
              {user.backup_enabled ? "BACKUP ATTIVO" : "ATTIVA BACKUP"}
            </button>
            {bk?.drive_connected && (
              <button onClick={runBackup} disabled={bkBusy} className="btn-ghost border border-rule rounded-sm px-3 py-2 text-sm disabled:opacity-50" data-testid="run-backup-btn">
                <RefreshCw size={14} className={bkBusy ? "animate-spin" : ""} /> Esegui backup ora
              </button>
            )}
            {user.is_admin && bk?.drive_connected && (
              <button onClick={testBackup} disabled={bkBusy} className="btn-ghost border border-rule rounded-sm px-3 py-2 text-sm disabled:opacity-50" data-testid="test-backup-btn">
                <FlaskConical size={14} /> Testa backup
              </button>
            )}
          </div>
        </div>

        {!user.backup_enabled && (
          <div className="mt-3 flex items-start gap-2 text-highlightFg bg-highlight border border-[#FDE047] p-3 rounded-sm text-sm">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" />
            <span>Backup disattivato. I file sono solo sul server. Se viene perso, dovrai ricaricarli manualmente.</span>
          </div>
        )}
      </Section>

      <Section title="ZONA PERICOLO" testId="settings-danger-section">
        <button onClick={deleteAccount} className="inline-flex items-center gap-2 border border-red-500 text-red-600 hover:bg-red-50 px-4 py-2 rounded-sm font-medium text-sm" data-testid="settings-delete-account">
          <Trash2 size={14} /> Cancella account
        </button>
      </Section>
    </div>
  );
}

function Stat({ label, value, mono }) {
  return (
    <div>
      <div className="overline mb-1">{label}</div>
      <div className={`text-2xl font-bold ${mono ? "text-mono" : "font-display"}`}>{value}</div>
    </div>
  );
}
