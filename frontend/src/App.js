import React, { useEffect, useRef, useState } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from "react-router-dom";
import { Toaster } from "sonner";

import api from "@/lib/api";
import { getGoogleRedirectUri } from "@/lib/google";

import { AuthProvider, useAuth } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Header from "@/components/Header";
import BackupBanner from "@/components/BackupBanner";

import Login from "@/pages/Login";
import Register from "@/pages/Register";
import ForgotPassword from "@/pages/ForgotPassword";
import ResetPassword from "@/pages/ResetPassword";
import ProfileSetup from "@/pages/ProfileSetup";
import Home from "@/pages/Home";
import Library from "@/pages/Library";
import SharedLibraries from "@/pages/SharedLibraries";
import SharedLibraryDetail from "@/pages/SharedLibraryDetail";
import SharedView from "@/pages/SharedView";
import PdfViewer from "@/pages/PdfViewer";
import Settings from "@/pages/Settings";
import AdminLogs from "@/pages/AdminLogs";
import Admin from "@/pages/Admin";
import ErrorBoundary from "@/components/ErrorBoundary";
import { shouldHideAppChrome } from "@/viewer/viewerChrome";

function GoogleOAuthReturn() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setUser, loginWithToken, user: currentUser } = useAuth();
  const processed = useRef(false);
  const [status, setStatus] = useState("Connessione a Google in corso...");

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;

    const params = new URLSearchParams(location.search);
    const code = params.get("code");
    const error = params.get("error");
    const mode = sessionStorage.getItem("google_oauth_mode") || "login";
    sessionStorage.removeItem("google_oauth_mode");

    if (error) {
      navigate("/login", { replace: true });
      return;
    }

    if (!code) {
      navigate("/login", { replace: true });
      return;
    }

    (async () => {
      try {
        const redirect_uri = getGoogleRedirectUri();

        const r = await api.post("/auth/google", {
          code,
          redirect_uri,
        });

        loginWithToken(r.data.token, r.data.user);
        navigate(r.data.user.profile_completed ? "/" : "/profile-setup", { replace: true });
      } catch (e) {
        setStatus("Errore Google");
        setTimeout(() => navigate("/login", { replace: true }), 2000);
      }
    })();
  }, []);

  return <div>{status}</div>;
}

function ChromeWrapper({ children }) {
  const { user } = useAuth();
  const location = useLocation();
  const noChrome = shouldHideAppChrome(location.pathname);

  return (
    <>
      {user && !noChrome && <BackupBanner />}
      {user && !noChrome && <Header />}
      {children}
    </>
  );
}

function AppShell() {
  const location = useLocation();

  const params = new URLSearchParams(location.search);
  if (params.get("code") || params.get("error")) {
    return <GoogleOAuthReturn />;
  }

  return (
    <ChromeWrapper>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot" element={<ForgotPassword />} />
        <Route path="/reset" element={<ResetPassword />} />

        <Route path="/profile-setup" element={<ProtectedRoute requireProfile={false}><ProfileSetup /></ProtectedRoute>} />
        <Route path="/" element={<ProtectedRoute><Home /></ProtectedRoute>} />
        <Route path="/library" element={<ProtectedRoute><Library /></ProtectedRoute>} />
        <Route path="/libraries" element={<ProtectedRoute><SharedLibraries /></ProtectedRoute>} />
        <Route path="/libraries/:id" element={<ProtectedRoute><SharedLibraryDetail /></ProtectedRoute>} />
        <Route path="/shared/:token" element={<SharedView />} />
        <Route path="/viewer/:id" element={<ProtectedRoute><PdfViewer /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/logs" element={<ProtectedRoute><AdminLogs /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute><Admin /></ProtectedRoute>} />
      </Routes>
    </ChromeWrapper>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppShell />
        <Toaster />
      </BrowserRouter>
    </AuthProvider>
  );
}
