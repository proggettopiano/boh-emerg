import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";

export default function GoogleCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setUser, loginWithToken, user: currentUser } = useAuth();
  const processed = useRef(false);
  const [status, setStatus] = useState("Connessione a Google in corso…");

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;
    const code = params.get("code");
    const state = params.get("state");
    const err = params.get("error");
    if (err) { toast.error("Google: " + err); navigate("/login"); return; }
    if (!code) { navigate("/login"); return; }
    const redirectUri =
      process.env.REACT_APP_GOOGLE_REDIRECT_URI ||
      window.location.origin + "/auth/google/callback";
    const mode = sessionStorage.getItem("google_oauth_mode") || "login";
    sessionStorage.removeItem("google_oauth_mode");

    (async () => {
      try {
        if (mode === "master") {
          const r = await api.post("/admin/master-drive/connect", { code, redirect_uri: redirectUri });
          toast.success(`Master Drive connesso: ${r.data.email}`);
          navigate("/admin", { replace: true });
        } else if (mode === "connect") {
          // Connect Drive to existing logged-in user
          const r = await api.post("/auth/google/connect", { code, redirect_uri: redirectUri });
          setUser(r.data);
          toast.success("Google Drive connesso!");
          navigate("/settings", { replace: true });
        } else {
          const r = await api.post("/auth/google", { code, redirect_uri: redirectUri });
          loginWithToken(r.data.token, r.data.user);
          toast.success("Accesso effettuato");
          navigate(r.data.user.profile_completed ? "/" : "/profile-setup", { replace: true });
        }
      } catch (e) {
        const msg = e.response?.data?.detail || "Errore Google";
        setStatus(msg);
        toast.error(msg);
        setTimeout(() => navigate(currentUser ? (mode === "master" ? "/admin" : "/settings") : "/login"), 2500);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center text-mono text-sm text-muted2" data-testid="google-callback-page">
      {status}
    </div>
  );
}
