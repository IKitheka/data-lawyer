// src/background.ts
import { analyzePolicy } from "./api/policies";

const API_TIMEOUT_MS = 20000;

// ---- helpers ----
async function sha256(text: string): Promise<string> {
  const enc = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function keyForUrl(url: string) {
  return (url || "").trim().toLowerCase();
}

function withTimeout<T>(p: Promise<T>, timeoutMs: number) {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("Request timed out")), timeoutMs);

    p.then((v) => {
      clearTimeout(t);
      resolve(v);
    }).catch((e) => {
      clearTimeout(t);
      reject(e);
    });
  });
}

// ---- main listener ----
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "ANALYZE_POLICY") return;

  (async () => {
    try {
      const url: string = String(message.url || "");
      const text: string = String(message.text || "");
      const title: string | undefined = message.title ? String(message.title) : undefined;

      if (!url || !text) {
        sendResponse({ ok: false, error: "Missing url/text in message." });
        return;
      }

      // 1) Hash the policy text
      const urlKey = keyForUrl(url);
      const newHash = await sha256(text);

      // 2) Load previous snapshot + changelog
      const stored = await chrome.storage.local.get(["policySnapshots", "changeLog"]);
      const policySnapshots: Record<string, any> = stored.policySnapshots || {};
      const changeLog: any[] = stored.changeLog || [];

      const prev = policySnapshots[urlKey];
      const changed = !!prev && prev.last_hash !== newHash;

      // 3) If changed, log an event
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

      // 4) Update snapshot for next time
      policySnapshots[urlKey] = {
        last_hash: newHash,
        last_seen: new Date().toISOString(),
        last_url: url,
        last_title: title,
      };

      // 5) Call backend (with timeout)
      const data = await withTimeout(
        analyzePolicy({
          url,
          text,
          service_name: message.service_name || undefined,
        }),
        API_TIMEOUT_MS
      );

      // 6) Save latest analysis + change status for popup
      const latestAnalysis = {
        ...data,
        url,
        title,
        saved_at: new Date().toISOString(),
        policy_hash: newHash,
        changed_since_last: changed,
      };

      await chrome.storage.local.set({
        latestAnalysis,
        policySnapshots,
        changeLog,
      });

      sendResponse({ ok: true, data: latestAnalysis });
    } catch (err: any) {
      sendResponse({ ok: false, error: String(err?.message || err) });
    }
  })();

  return true; // async response
});
