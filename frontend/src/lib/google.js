import api from "@/lib/api";

const GOOGLE_REDIRECT_URI =
  import.meta.env.VITE_GOOGLE_REDIRECT_URI ||
  window.location.origin + "/auth/google/callback";

let googleOAuthInProgress = false;

export async function startGoogleOAuth(mode = "login") {
  if (googleOAuthInProgress) return;
  googleOAuthInProgress = true;

  try {
    const redirectUri = GOOGLE_REDIRECT_URI;
    sessionStorage.setItem("google_oauth_mode", mode);

    const endpoint = mode === "master" ? "/admin/master-drive/url" : "/auth/google/url";
    const r = await api.post(endpoint, { redirect_uri: redirectUri });

    const url = r?.data?.url;
    if (!url) {
      throw new Error("Google OAuth URL non disponibile");
    }

    window.location.href = url;
  } finally {
    googleOAuthInProgress = false;
  }
}
