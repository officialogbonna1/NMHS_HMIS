import axios from "axios";

/**
 * Where the API lives. Unset (local development): `/api` on this origin, which
 * Vite proxies to Django. Set at build time when the app is served from its own
 * origin (a Render Static Site): `VITE_API_BASE_URL=https://api.example.org`
 * makes every request go to `https://api.example.org/api/…`. It is a public
 * address baked into the bundle — never a secret.
 */
export function apiBaseUrl(origin) {
  const base = (origin ?? "").trim().replace(/\/+$/, "");
  return base ? `${base}/api` : "/api";
}

const api = axios.create({ baseURL: apiBaseUrl(import.meta.env.VITE_API_BASE_URL) });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("authToken");
  if (token) config.headers.Authorization = `Token ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("authToken");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
