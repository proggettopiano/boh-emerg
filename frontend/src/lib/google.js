import api from "@/lib/api";

const GOOGLE_REDIRECT_URI =
  process.env.REACT_APP_GOOGLE_REDIRECT_URI ||
  window.location.origin + "/auth/google/callback";

export async function startGoogleOAuth(mode = "login") {
  const redirectUri = GOOGLE_REDIRECT_URI;
  sessionStorage.setItem("google_oauth_mode", mode);
  if (mode === "master") {
    const r = await api.post("/admin/master-drive/url", { redirect_uri: redirectUri });
    window.location.href = r.data.url;
    return;
  }
  const r = await api.post("/auth/google/url", { redirect_uri: redirectUri });
  window.location.href = r.data.url;
}
