import axios from "axios";

const BACKEND_URL = import.meta.env.VITE_API_URL || import.meta.env.VITE_BACKEND_URL;
if (!BACKEND_URL) {
  throw new Error(
    "Environment variable VITE_API_URL must be defined for frontend API requests"
  );
}

export const API = `${BACKEND_URL.replace(/\/+$|\s+$/g, "")}/api`;

const api = axios.create({
  baseURL: API,
  timeout: Number(import.meta.env.VITE_API_TIMEOUT_MS || 20000),
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("scorelib_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export default api;
export { BACKEND_URL };
