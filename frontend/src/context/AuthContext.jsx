import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import api from "@/lib/api";

const AUTH_TOKEN_KEY = "scorelib_session_token";
const LEGACY_AUTH_TOKEN_KEY = "scorelib_token";

function getAuthToken() {
  const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
  if (token) return token;
  const legacy = localStorage.getItem(LEGACY_AUTH_TOKEN_KEY);
  if (!legacy) return null;
  sessionStorage.setItem(AUTH_TOKEN_KEY, legacy);
  localStorage.removeItem(LEGACY_AUTH_TOKEN_KEY);
  return legacy;
}

function saveAuthToken(token) {
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.removeItem(LEGACY_AUTH_TOKEN_KEY);
}

function clearAuthToken() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(LEGACY_AUTH_TOKEN_KEY);
}

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(false);

  const fetchMe = useCallback(async (signal) => {
    const token = getAuthToken();
    if (!token) {
      if (mountedRef.current) {
        setUser(null);
        setLoading(false);
      }
      return;
    }
    try {
      const r = await api.get("/auth/me", signal ? { signal } : undefined);
      if (mountedRef.current && !signal.aborted) setUser(r.data);
    } catch (e) {
      if (e.name === "CanceledError" || e.name === "AbortError" || e.code === "ERR_CANCELED" || e.isCancel) return;
      clearAuthToken();
      if (mountedRef.current) setUser(null);
    } finally {
      if (mountedRef.current && !signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const ctrl = new AbortController();
    fetchMe(ctrl.signal);
    return () => {
      mountedRef.current = false;
      ctrl.abort();
    };
  }, [fetchMe]);

  const loginWithToken = (token, u) => {
    saveAuthToken(token);
    setUser(u);
  };

  const logout = () => {
    clearAuthToken();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, loading, loginWithToken, logout, refresh: fetchMe }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
