import { API_BASE } from "../config/api";

const KEY_STORAGE = "DL_API_KEY";
const CLIENT_ID_KEY = "dl_client_id";

function newClientId() {
  return `dl_${crypto.randomUUID()}`;
}

async function getClientId(): Promise<string> {
  const res = await chrome.storage.local.get([CLIENT_ID_KEY]);
  if (typeof res?.[CLIENT_ID_KEY] === "string") return res[CLIENT_ID_KEY];

  const id = newClientId();
  await chrome.storage.local.set({ [CLIENT_ID_KEY]: id });
  return id;
}

// Public beta key (free tier)
async function getApiKey(): Promise<string> {
  const res = await chrome.storage.local.get([KEY_STORAGE]);

  // auto inject public beta key if missing
  if (!res?.[KEY_STORAGE]) {
    const beta = "dl_public_beta_2026";
    await chrome.storage.local.set({ [KEY_STORAGE]: beta });
    return beta;
  }

  return String(res[KEY_STORAGE]).trim();
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const [clientId, apiKey] = await Promise.all([getClientId(), getApiKey()]);

  if (!apiKey) throw new Error("Missing API key (public beta key not set)");

  const headers = new Headers(init.headers || {});
  headers.set("Content-Type", "application/json");
  headers.set("X-Client-Id", clientId);
  headers.set("X-API-Key", apiKey);

  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
