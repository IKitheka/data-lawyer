// src/api/clientID.ts
const CLIENT_ID_KEY = "dl_client_id";

function newClientId() {
  return `dl_${crypto.randomUUID()}`;
}

export async function getClientId(): Promise<string> {
  const res = await chrome.storage.local.get([CLIENT_ID_KEY]);
  const existing = res?.[CLIENT_ID_KEY];

  if (typeof existing === "string" && existing.length > 8) return existing;

  const id = newClientId();
  await chrome.storage.local.set({ [CLIENT_ID_KEY]: id });
  return id;
}
