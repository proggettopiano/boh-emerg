import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import AuthShell from "@/components/AuthShell";

export default function Register() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const navigate = useNavigate();
  const { loginWithToken } = useAuth();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const start = Date.now();
      const r = await api.post("/auth/register", { email, password });
      console.log(`Register took ${Date.now() - start}ms`);
      if (r.data.status === "verification_email_sent") {
        toast.success("Account creato! Controlla la tua email per verificare.");
        navigate("/login", { replace: true });
      } else if (r.data.status === "verification_pending") {
        toast.success("Account creato! Usa 'Reinvia email' per ricevere il link.");
        navigate("/resend-verification", { replace: true, state: { email } });
      }
      setRetryCount(0); // success, reset
    } catch (e) {
      const msg = e.response?.data?.detail || "Errore di registrazione";
      console.error("Register failed:", e);
      if (retryCount < 2 && (e.code === 'NETWORK_ERROR' || e.response?.status >= 500)) {
        setRetryCount(c => c + 1);
        toast.error(`${msg}. Riprovo (${retryCount + 1}/2)...`);
        setTimeout(() => submit(e), 2000); // retry after 2s
        return;
      }
      toast.error(msg);
      if (e.response?.status === 409) navigate("/forgot?email=" + encodeURIComponent(email));
      setRetryCount(0);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell title="Crea account" subtitle="Il tuo spazio per ogni spartito." testId="register-page">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="overline block mb-2">Email</label>
          <input data-testid="register-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="input-base" placeholder="tu@esempio.com" />
        </div>
        <div>
          <label className="overline block mb-2">Password · min 6 caratteri</label>
          <input data-testid="register-password-input" type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="input-base" placeholder="••••••••" />
        </div>
        <button type="submit" disabled={busy} className="btn-primary w-full disabled:opacity-50" data-testid="register-submit-btn">
          {busy ? (retryCount > 0 ? `Riprovo (${retryCount}/2)…` : "Creazione…") : "Crea account"}
        </button>
      </form>
      <div className="mt-6 text-sm text-[#525252]">
        Hai già un account? <Link to="/login" className="underline hover:text-ink" data-testid="goto-login-link">Accedi</Link>
        <br />
        <Link to="/resend-verification" className="underline hover:text-ink">Reinvia email verifica</Link>
      </div>
    </AuthShell>
  );
}
