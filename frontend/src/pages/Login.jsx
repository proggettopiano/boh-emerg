import React, { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import AuthShell from "@/components/AuthShell";

export default function Login() {
  const [mode, setMode] = useState("request"); // default to request access
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { loginWithToken } = useAuth();

  const handleLogin = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.post("/auth/login", { email, password });
      
      if (r.data.action === "request_access") {
        setMode("request");
        setEmail(r.data.email);
        return;
      }

      loginWithToken(r.data.token, r.data.user);
      const from = location.state?.from || "/";
      navigate(from, { replace: true });
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Credenziali non valide");
    } finally {
      setBusy(false);
    }
  };

  const handleRequest = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/request-access", { name, email });
      setSuccess(true);
      toast.success("Richiesta inviata con successo!");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Errore nell'invio della richiesta");
    } finally {
      setBusy(false);
    }
  };

  if (success) {
    return (
      <AuthShell title="Richiesta Inviata" subtitle="Grazie per l'interesse.">
        <div className="rounded-2xl border border-emerald-100 bg-emerald-50 p-6 text-center space-y-4 py-6">
          <p className="text-ink text-base font-semibold">
            La tua richiesta di accesso per <strong>{email}</strong> è stata inoltrata.
          </p>
          <p className="text-muted3 text-sm">
            Potrai accedere non appena l'amministratore avrà approvato il tuo indirizzo email.
          </p>
          <button onClick={() => { setSuccess(false); setMode("login"); }} className="btn-secondary w-full">
            Torna al login
          </button>
        </div>
      </AuthShell>
    );
  }
  return (
    <AuthShell 
      title={mode === "login" ? "Accedi" : "Richiedi Accesso"}
      subtitle={mode === "login" ? "Accedi al tuo account" : "Richiedi l'accesso per visualizzare la libreria"}
    >
      {mode === "login" ? (
        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="overline block mb-2">Email</label>
            <input 
              type="email" required value={email} 
              onChange={(e) => setEmail(e.target.value)} 
              className="input-base" placeholder="tu@esempio.com" 
            />
          </div>
          <div>
            <label className="overline block mb-2">Password</label>
            <input 
              type="password" required value={password} 
              onChange={(e) => setPassword(e.target.value)} 
              className="input-base" placeholder="********" 
            />
          </div>

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Accesso in corso..." : "Accedi"}
          </button>
          
          <div className="pt-4 text-center space-y-2">
            <button 
              type="button" onClick={() => setMode("request")}
              className="text-sm text-ink hover:underline font-medium"
            >
              Non hai l'accesso? Richiedilo qui
            </button>
            <button
              type="button" onClick={() => setMode("login")}
              className="text-sm text-ink hover:underline font-medium"
            >
              Sei l'admin? Accedi qui
            </button>
          </div>
        </form>
      ) : (
        <form onSubmit={handleRequest} className="space-y-4">
          <div>
            <label className="overline block mb-2">Nome Completo</label>
            <input 
              type="text" required value={name} 
              onChange={(e) => setName(e.target.value)} 
              className="input-base" placeholder="Mario Rossi" 
            />
          </div>
          <div>
            <label className="overline block mb-2">Email</label>
            <input 
              type="email" required value={email} 
              onChange={(e) => setEmail(e.target.value)} 
              className="input-base" placeholder="tu@esempio.com" 
            />
          </div>
          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Invio richiesta..." : "Invia Richiesta"}
          </button>
          <div className="pt-4 text-center space-y-2">
            <button 
              type="button" onClick={() => setMode("login")}
              className="text-sm text-ink hover:underline font-medium"
            >
              Torna al login
            </button>
            <button
              type="button" onClick={() => setMode("login")}
              className="text-sm text-ink hover:underline font-medium"
            >
              Sei l'admin? Accedi qui
            </button>
          </div>
        </form>
      )}
    </AuthShell>
  );
}
