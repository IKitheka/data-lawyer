// src/api/clientId.ts
const KEY = "dl_client_id";

function genId() {
  // good enough for per-device id
  return `dl_${crypto.randomUUID()}`;
}

export async function getClientId(): Promise<string> {
  const stored = await chrome.storage.local.get([KEY]);
  const existing = stored?.[KEY];
  if (typeof existing === "string" && existing.length > 8) return existing;

  const id = genId();
  await chrome.storage.local.set({ [KEY]: id });
  return id;
}
