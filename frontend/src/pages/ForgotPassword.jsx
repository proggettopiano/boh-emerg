import React, { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import AuthShell from "@/components/AuthShell";

export default function ForgotPassword() {
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get("email") || "");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/forgot", { email });
      setSent(true);
      toast.success("Se l'email esiste, riceverai un link.");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Errore");
    } finally { setBusy(false); }
  };
  return (
    <AuthShell title="Password dimenticata" subtitle="Ti invieremo un link per reimpostarla." testId="forgot-page">
      {sent ? (
        <div className="bg-canvas2 border border-rule p-6 rounded-sm" data-testid="forgot-sent">
          <p className="text-sm text-[#525252]">
            Se l'email <span className="text-mono text-ink">{email}</span> è registrata, abbiamo inviato un link valido per 60 minuti. Controlla anche spam.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="overline block mb-2">Email</label>
            <input data-testid="forgot-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="input-base" placeholder="tu@esempio.com" />
          </div>
          <button type="submit" disabled={busy} className="btn-primary w-full disabled:opacity-50" data-testid="forgot-submit-btn">
            {busy ? "Invio…" : "Invia link"}
          </button>
        </form>
      )}
      <div className="mt-6 text-sm"><Link to="/login" className="underline text-[#525252] hover:text-ink">← Torna all'accesso</Link></div>
    </AuthShell>
  );
}
