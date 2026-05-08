import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export default function ProtectedRoute({ children, requireProfile = true }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-mono text-sm text-muted2" data-testid="auth-loading">
        Caricamento…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (requireProfile && !user.profile_completed && location.pathname !== "/profile-setup") {
    return <Navigate to="/profile-setup" replace />;
  }
  return children;
}
