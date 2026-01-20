// src/api/client.ts
const API_BASE = "https://data-lawyer.onrender.com";

const KEY_STORAGE = "DL_API_KEY";
const CLIENT_ID_KEY = "dl_client_id";

function newClientId() {
  return `dl_${crypto.randomUUID()}`;
}

async function getClientId(): Promise<string> {
  const res = await chrome.storage.local.get([CLIENT_ID_KEY]);
  const existing = res?.[CLIENT_ID_KEY];
  if (typeof existing === "string" && existing.length > 8) return existing;

  const id = newClientId();
  await chrome.storage.local.set({ [CLIENT_ID_KEY]: id });
  return id;
}

async function getApiKey(): Promise<string> {
  const res = await chrome.storage.local.get([KEY_STORAGE]);
  return String(res?.[KEY_STORAGE] ?? "").trim();
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const [clientId, apiKey] = await Promise.all([getClientId(), getApiKey()]);

  if (!apiKey) {
    throw new Error("Missing API key in extension storage (DL_API_KEY).");
  }

  const headers = new Headers(init.headers || {});
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  headers.set("X-Client-Id", clientId);
  headers.set("X-API-Key", apiKey);

  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
