import React, { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import AuthShell from "@/components/AuthShell";
import { startGoogleOAuth } from "@/lib/google";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { loginWithToken } = useAuth();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setRetrying(false);
    try {
      let lastError;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const r = await api.post("/auth/login", { email, password });
          loginWithToken(r.data.token, r.data.user);
          const nextParam = new URLSearchParams(location.search).get("next");
          const safeNext = nextParam?.startsWith("/") && !nextParam.startsWith("//") ? nextParam : null;
          const from = location.state?.from || safeNext || (r.data.user.profile_completed ? "/" : "/profile-setup");
          navigate(from, { replace: true });
          return;
        } catch (err) {
          lastError = err;
          const retryable = err.code === "ERR_NETWORK" || err.code === "ECONNABORTED" || err.response?.status >= 500;
          if (!retryable || attempt === 2) break;
          setRetrying(true);
          await new Promise((resolve) => setTimeout(resolve, 1200 + attempt * 800));
        }
      }
      throw lastError;
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Errore di accesso");
    } finally {
      setBusy(false);
      setRetrying(false);
    }
  };

  const googleLogin = async () => {
    try {
      await startGoogleOAuth("login");
    } catch (e) {
      toast.error("Google OAuth non disponibile");
    }
  };

  return (
    <AuthShell title="Accedi" subtitle="Apri la tua libreria di spartiti." testId="login-page">
      <button onClick={googleLogin} className="w-full border-2 border-ink py-3 rounded-md font-medium flex items-center justify-center gap-3 hover:bg-canvas3 transition-colors mb-6" data-testid="login-google-btn">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.4 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.3 6.1 29.4 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.3 7.1 29.4 5 24 5 16.3 5 9.7 9.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 10-2 13.6-5.2l-6.3-5.2c-2 1.4-4.6 2.4-7.3 2.4-5.3 0-9.7-3.5-11.3-8.4l-6.6 5.1C9.6 39.7 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4 5.6l6.3 5.2C42 35 44 30 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        Continua con Google
      </button>
      <div className="relative my-6"><div className="absolute inset-0 flex items-center"><div className="w-full border-t border-rule"></div></div><div className="relative flex justify-center"><span className="bg-white px-3 text-mono text-xs uppercase tracking-widest text-muted3">oppure</span></div></div>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="overline block mb-2">Email</label>
          <input data-testid="login-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="input-base" placeholder="tu@esempio.com" />
        </div>
        <div>
          <label className="overline block mb-2">Password</label>
          <input data-testid="login-password-input" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className="input-base" placeholder="********" />
        </div>
        <button type="submit" disabled={busy} className="btn-primary w-full disabled:opacity-50" data-testid="login-submit-btn">
          {busy ? (retrying ? "Risveglio backend..." : "Accesso...") : "Accedi"}
        </button>
      </form>
      <div className="mt-6 text-center text-sm text-muted3">
        Accedi con l'account condiviso del gruppo.
      </div>
    </AuthShell>
  );
}
