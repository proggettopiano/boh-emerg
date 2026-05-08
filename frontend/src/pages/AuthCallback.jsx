import React, { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function AuthCallback() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;
    const hash = window.location.hash || "";
    const m = hash.match(/session_id=([^&]+)/);
    const sessionId = m ? m[1] : null;
    if (!sessionId) { navigate("/login"); return; }
    (async () => {
      try {
        const r = await api.post("/auth/google", { session_id: sessionId });
        localStorage.setItem("scorelib_token", r.data.token);
        setUser(r.data.user);
        window.history.replaceState(null, "", "/");
        if (!r.data.user.profile_completed) navigate("/profile-setup", { replace: true });
        else navigate("/", { replace: true });
      } catch (e) {
        navigate("/login?error=google");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center text-mono text-sm text-muted2" data-testid="auth-callback">
      Autenticazione in corso…
    </div>
  );
}
