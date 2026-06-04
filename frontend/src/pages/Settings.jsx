import React, { useState, useEffect } from "react";
import { toast } from "sonner";
import { HardDriveUpload, RefreshCw, CheckCircle2, CloudOff } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function Section({ title, children }) {
  return (
    <section className="border-t border-rule py-8">
      <h2 className="overline mb-4">{title}</h2>
      {children}
    </section>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const [bk, setBk] = useState(null);
  const [bkBusy, setBkBusy] = useState(false);

  const loadBackup = async () => {
    try { const r = await api.get("/backup/status"); setBk(r.data); } catch {}
  };
  useEffect(() => { loadBackup(); }, []);

  const runBackup = async () => {
    setBkBusy(true);
    try { 
      const r = await api.post("/backup/run"); 
      toast.success(`Backup avviato · ${r.data.pending} file in attesa`); 
      loadBackup(); 
    }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
    finally { setBkBusy(false); }
  };

  if (!user) return null;

  return (
    <div className="max-w-3xl mx-auto px-6 md:px-12 py-12">
      <p className="overline mb-2">ACCOUNT</p>
      <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter mb-10">Impostazioni</h1>

      <Section title="ACCOUNT">
        <p className="text-sm text-muted2 mb-4">
          Stai usando un account di gruppo. Le impostazioni di sicurezza sono gestite dall'amministratore.
        </p>
        <div className="text-mono text-sm text-muted2">Email: {user.email}</div>
        <div className="text-mono text-sm text-muted2">Ruolo: {user.is_admin ? "Amministratore" : "Membro Gruppo"}</div>
      </Section>

      <Section title="BACKUP GRUPPO">
        <div className="border border-rule rounded-md p-5 space-y-4 bg-white">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <p className="font-medium flex items-center gap-2">
                {bk?.drive_connected ? <><CheckCircle2 size={16} className="text-emerald-600" /> Sincronizzazione Cloud Attiva</> : <><CloudOff size={16} className="text-muted2" /> Backup non configurato</>}
              </p>
              <p className="text-sm text-muted2 mt-1">
                Tutti gli spartiti sono salvati automaticamente nel Master Drive di gruppo.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 border-t border-rule pt-4">
            <Stat label="Libreria" value="GRUPPO" mono />
            <Stat label="File totali" value={bk?.total_pdfs ?? "—"} />
            <Stat label="Su Drive" value={bk?.backed_up_pdfs ?? "—"} />
            <Stat label="In attesa" value={bk?.pending_pdfs ?? "—"} />
          </div>

          {user.is_admin && bk?.drive_connected && (
            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-rule">
              <button onClick={runBackup} disabled={bkBusy} className="btn-ghost border border-rule rounded-sm px-3 py-2 text-sm disabled:opacity-50">
                <RefreshCw size={14} className={bkBusy ? "animate-spin" : ""} /> Sincronizza ora
              </button>
            </div>
          )}
        </div>
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
