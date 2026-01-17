import { getClientId } from "./clientId";

const API_BASE = "https://data-lawyer.onrender.com";

// IMPORTANT: In Vite, env vars must start with VITE_
const API_KEY = import.meta.env.VITE_DL_API_KEY as string | undefined;

export async function apiFetch(path: string, init: RequestInit = {}) {
  const clientId = await getClientId();

  const headers = new Headers(init.headers || {});
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  // per-device/user header (for rate limiting)
  headers.set("X-Client-Id", clientId);

  // API key auth header
  if (API_KEY) headers.set("X-API-Key", API_KEY);

  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
