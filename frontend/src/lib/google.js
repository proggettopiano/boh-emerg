import api from "@/lib/api";

export async function startGoogleOAuth(mode = "login") {
  const redirectUri = window.location.origin + "/auth/google/callback";
  sessionStorage.setItem("google_oauth_mode", mode);
  const r = await api.post("/auth/google/url", { redirect_uri: redirectUri });
  window.location.href = r.data.url;
}
