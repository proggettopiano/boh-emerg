import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import api from "@/lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchMe = useCallback(async () => {
    const token = localStorage.getItem("scorelib_token");
    if (!token) { setUser(null); setLoading(false); return; }
    try {
      const r = await api.get("/auth/me");
      setUser(r.data);
    } catch {
      localStorage.removeItem("scorelib_token");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (window.location.hash?.includes("session_id=")) {
      setLoading(false);
      return;
    }
    fetchMe();
  }, [fetchMe]);

  const loginWithToken = (token, u) => {
    localStorage.setItem("scorelib_token", token);
    setUser(u);
  };

  const logout = () => {
    localStorage.removeItem("scorelib_token");
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, loading, loginWithToken, logout, refresh: fetchMe }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
