import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const HOW_FOUND = ["Google", "Amico/Collega", "Social media", "YouTube", "Forum musicali", "Altro"];

export default function ProfileSetup() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.name || "");
  const [howFound, setHowFound] = useState(user?.how_found || "");
  const [picture, setPicture] = useState(user?.picture || "");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const onPickFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) { toast.error("Immagine troppo grande (max 2MB)"); return; }
    const reader = new FileReader();
    reader.onload = () => setPicture(reader.result);
    reader.readAsDataURL(f);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Inserisci il tuo nome"); return; }
    setBusy(true);
    try {
      const r = await api.patch("/profile", { name: name.trim(), how_found: howFound, picture, profile_completed: true });
      setUser(r.data);
      toast.success("Profilo salvato");
      navigate("/", { replace: true });
    } catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6 md:p-12 bg-canvas">
      <div className="w-full max-w-xl" data-testid="profile-setup-page">
        <p className="overline mb-3">PROFILO · STEP 1</p>
        <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter mb-3">Benvenuto.</h1>
        <p className="text-[#525252] mb-10">Compila qualche dettaglio per personalizzare la tua esperienza.</p>
        <form onSubmit={submit} className="space-y-6">
          <div className="flex items-center gap-5">
            <div className="w-20 h-20 rounded-md bg-canvas3 overflow-hidden border border-rule flex items-center justify-center">
              {picture ? <img src={picture} alt="profile" className="w-full h-full object-cover" /> : <span className="text-mono text-xs text-muted2">FOTO</span>}
            </div>
            <label className="btn-ghost border border-rule rounded-sm cursor-pointer">
              <input type="file" accept="image/*" className="hidden" onChange={onPickFile} data-testid="profile-picture-input" />
              {picture ? "Cambia foto" : "Carica foto"}
            </label>
          </div>
          <div>
            <label className="overline block mb-2">Nome (visibile nelle condivisioni)</label>
            <input data-testid="profile-name-input" required value={name} onChange={(e) => setName(e.target.value)} className="input-base" placeholder="Es. Maria Rossi" />
          </div>
          <div>
            <label className="overline block mb-2">Come ci hai trovato?</label>
            <div className="flex flex-wrap gap-2" data-testid="how-found-group">
              {HOW_FOUND.map((h) => (
                <button type="button" key={h} onClick={() => setHowFound(h)} className={`px-3 py-2 text-sm rounded-sm border transition-colors ${howFound === h ? "bg-ink text-white border-ink" : "border-rule text-[#525252] hover:border-ink hover:text-ink"}`} data-testid={`how-found-${h.toLowerCase().replace(/[^a-z]/g, "-")}`}>{h}</button>
              ))}
            </div>
          </div>
          <button type="submit" disabled={busy} className="btn-primary disabled:opacity-50" data-testid="profile-save-btn">{busy ? "Salvataggio…" : "Continua"}</button>
        </form>
      </div>
    </div>
  );
}
