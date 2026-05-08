import React from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { Toaster } from "sonner";

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
import GoogleCallback from "@/pages/GoogleCallback";
import AuthCallback from "@/pages/AuthCallback";

function ChromeWrapper({ children }) {
  const { user } = useAuth();
  const location = useLocation();
  const noChrome = ["/login", "/register", "/forgot", "/reset", "/profile-setup", "/admin/logs", "/auth/callback", "/auth/google/callback"].some((p) => location.pathname.startsWith(p));
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
  // Process Google session_id from URL fragment first
  if (location.hash?.includes("session_id=")) return <AuthCallback />;
  return (
    <ChromeWrapper>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot" element={<ForgotPassword />} />
        <Route path="/reset" element={<ResetPassword />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/auth/google/callback" element={<GoogleCallback />} />
        <Route path="/admin/logs" element={<AdminLogs />} />

        <Route path="/profile-setup" element={<ProtectedRoute requireProfile={false}><ProfileSetup /></ProtectedRoute>} />
        <Route path="/" element={<ProtectedRoute><Home /></ProtectedRoute>} />
        <Route path="/library" element={<ProtectedRoute><Library /></ProtectedRoute>} />
        <Route path="/libraries" element={<ProtectedRoute><SharedLibraries /></ProtectedRoute>} />
        <Route path="/libraries/:id" element={<ProtectedRoute><SharedLibraryDetail /></ProtectedRoute>} />
        <Route path="/shared/:token" element={<ProtectedRoute requireProfile={false}><SharedView /></ProtectedRoute>} />
        <Route path="/viewer/:id" element={<ProtectedRoute><PdfViewer /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
      </Routes>
    </ChromeWrapper>
  );
}

export default function App() {
  return (
    <div className="App">
      <AuthProvider>
        <BrowserRouter>
          <AppShell />
          <Toaster position="bottom-right" theme="light" richColors closeButton />
        </BrowserRouter>
      </AuthProvider>
    </div>
  );
}
