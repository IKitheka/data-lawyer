// src/background.ts
import { analyzePolicy } from "./api/policies";

const API_TIMEOUT_MS = 90000; // ✅ 90s (Render cold start can be 30–60s)

function keyForUrl(url: string) {
  return (url || "").trim().toLowerCase();
}

async function sha256(text: string): Promise<string> {
  const enc = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function analyzeWithAbort(payload: { url: string; text: string; service_name?: string }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

  try {
    // IMPORTANT: analyzePolicy must pass signal through apiFetch → fetch
    return await analyzePolicy(payload, controller.signal);
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "ANALYZE_POLICY") return;

  (async () => {
    try {
      const url = String(message.url || "");
      const text = String(message.text || "");
      const title = message.title ? String(message.title) : undefined;

      if (!url || !text) return sendResponse({ ok: false, error: "Missing url/text in message." });

      const urlKey = keyForUrl(url);
      const newHash = await sha256(text);

      const stored = await chrome.storage.local.get(["policySnapshots", "changeLog"]);
      const policySnapshots: Record<string, any> = stored.policySnapshots || {};
      const changeLog: any[] = stored.changeLog || [];

      const prev = policySnapshots[urlKey];
      const changed = !!prev && prev.last_hash !== newHash;

      if (changed && prev) {
        changeLog.unshift({
          url,
          title,
          old_hash: prev.last_hash,
          new_hash: newHash,
          changed_at: new Date().toISOString(),
        });
        if (changeLog.length > 30) changeLog.length = 30;
      }

      policySnapshots[urlKey] = {
        last_hash: newHash,
        last_seen: new Date().toISOString(),
        last_url: url,
        last_title: title,
      };

      console.log("[Data Lawyer] calling backend...", { url, textLen: text.length });

      const data = await analyzeWithAbort({
        url,
        text,
        service_name: message.service_name || undefined,
      });

      const latestAnalysis = {
        ...data,
        url,
        title,
        saved_at: new Date().toISOString(),
        policy_hash: newHash,
        changed_since_last: changed,
      };

      await chrome.storage.local.set({ latestAnalysis, policySnapshots, changeLog });

      console.log("[Data Lawyer] backend OK", latestAnalysis);
      sendResponse({ ok: true, data: latestAnalysis });
    } catch (err: any) {
      const msg =
        err?.name === "AbortError"
          ? `Request timed out after ${API_TIMEOUT_MS / 1000}s (backend slow/cold start)`
          : String(err?.message || err);

      console.error("[Data Lawyer] backend FAIL:", msg);
      sendResponse({ ok: false, error: msg });
    }
  })();

  return true;
});
