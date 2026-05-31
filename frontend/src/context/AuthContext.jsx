import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import api from "@/lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(false);

  const fetchMe = useCallback(async (signal) => {
    const token = localStorage.getItem("scorelib_token");
    if (!token) {
      if (mountedRef.current) {
        setUser(null);
        setLoading(false);
      }
      return;
    }
    try {
      const r = await api.get("/auth/me", signal ? { signal } : undefined);
      if (mountedRef.current) setUser(r.data);
    } catch (e) {
      if (e.name === "CanceledError" || e.name === "AbortError" || e.code === "ERR_CANCELED") return;
      localStorage.removeItem("scorelib_token");
      if (mountedRef.current) setUser(null);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (window.location.hash?.includes("session_id=")) {
      setLoading(false);
      return () => { mountedRef.current = false; };
    }
    const ctrl = new AbortController();
    const timeout = window.setTimeout(() => ctrl.abort(), Number(process.env.REACT_APP_AUTH_TIMEOUT_MS || 12000));
    fetchMe(ctrl.signal).finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      mountedRef.current = false;
      ctrl.abort();
    };
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
