// src/api/client.ts
import { getClientId } from "./clientId";

const API_BASE = "https://data-lawyer.onrender.com";

export async function apiFetch(path: string, init: RequestInit = {}) {
  const clientId = await getClientId(); // ✅ USED (fixes TS6133)

  const headers = new Headers(init.headers || {});
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  headers.set("X-Client-Id", clientId);

  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
