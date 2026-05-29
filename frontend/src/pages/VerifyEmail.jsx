import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import AuthShell from "@/components/AuthShell";

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const navigate = useNavigate();
  const { loginWithToken } = useAuth();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) {
      setError("Token mancante");
      setBusy(false);
      return;
    }
    api.get(`/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then((r) => {
        loginWithToken(r.data.token, r.data.user);
        toast.success("Email verificata! Benvenuto.");
        navigate("/", { replace: true });
      })
      .catch((e) => {
        const msg = e.response?.data?.detail || "Errore nella verifica";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setBusy(false));
  }, [token, loginWithToken, navigate]);

  if (busy) {
    return (
      <AuthShell title="Verifica email" subtitle="Stiamo verificando il tuo account...">
        <div className="text-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-ink mx-auto"></div>
          <p className="mt-4 text-muted2">Verifica in corso...</p>
        </div>
      </AuthShell>
    );
  }

  if (error) {
    return (
      <AuthShell title="Verifica email" subtitle="C'è stato un problema">
        <div className="text-center py-8">
          <p className="text-red-600 mb-4">{error}</p>
          <button onClick={() => navigate("/login")} className="btn-ghost border border-rule rounded-sm px-4 py-2">
            Vai al login
          </button>
        </div>
      </AuthShell>
    );
  }

  return null;
}