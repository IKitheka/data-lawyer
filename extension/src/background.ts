// src/background.ts
import { analyzePolicy } from "./api/policies";

const REQUEST_TIMEOUT_MS = 75000;
const MIN_GAP_MS = 6000; // at most 1 request per 6s per tab

type InFlightEntry = {
  lastStart: number;
  promise: Promise<any>;
};

const inFlightByTab = new Map<number, InFlightEntry>();

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("Request timed out")), ms);
    p.then((v) => {
      clearTimeout(t);
      resolve(v);
    }).catch((e) => {
      clearTimeout(t);
      reject(e);
    });
  });
}

async function sha256Hex(text: string) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function normUrl(url: string) {
  return (url || "").trim().toLowerCase();
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Open the full report UI (extension page)
  if (msg?.type === "OPEN_REPORT") {
    chrome.tabs.create({ url: chrome.runtime.getURL("index.html") });
    sendResponse?.({ ok: true });
    return true;
  }

  if (msg?.type !== "ANALYZE_POLICY") return;

  (async () => {
    try {
      const tabId = sender.tab?.id ?? -1;
      const url = String(msg.url || "");
      const text = String(msg.text || "");
      const title = msg.title ? String(msg.title) : undefined;

      if (!url || !text) {
        sendResponse({ ok: false, error: "Missing url/text in message." });
        return;
      }

      // ✅ prevent spamming the API: 1 in-flight per tab + min gap
      if (tabId >= 0) {
        const existing = inFlightByTab.get(tabId);
        const now = Date.now();

        if (existing) {
          // If a request is already running, just wait for it
          const data = await existing.promise;
          sendResponse({ ok: true, data, reused: true });
          return;
        }

        // If last request was too recent, block quickly
        const lastStart = (await chrome.storage.local.get([`dl_last_${tabId}`]))[`dl_last_${tabId}`];
        if (typeof lastStart === "number" && now - lastStart < MIN_GAP_MS) {
          sendResponse({ ok: false, error: "Too many requests. Wait a moment then try again." });
          return;
        }

        await chrome.storage.local.set({ [`dl_last_${tabId}`]: now });
      }

      // Snapshot + change log
      const u = normUrl(url);
      const hash = await sha256Hex(text);

      const stored = await chrome.storage.local.get(["policySnapshots", "changeLog"]);
      const policySnapshots = stored.policySnapshots || {};
      const changeLog = stored.changeLog || [];

      const prev = policySnapshots[u];
      const changed = !!prev && prev.last_hash !== hash;

      if (changed && prev) {
        changeLog.unshift({
          url,
          title,
          old_hash: prev.last_hash,
          new_hash: hash,
          changed_at: new Date().toISOString(),
        });
        if (changeLog.length > 30) changeLog.length = 30;
      }

      policySnapshots[u] = {
        last_hash: hash,
        last_seen: new Date().toISOString(),
        last_url: url,
        last_title: title,
      };

      // ✅ actual API call
      const work = withTimeout(
        analyzePolicy({ url, text, service_name: msg.service_name || undefined }),
        REQUEST_TIMEOUT_MS
      );

      if (tabId >= 0) {
        inFlightByTab.set(tabId, { lastStart: Date.now(), promise: work });
      }

      const apiResult = await work.finally(() => {
        if (tabId >= 0) inFlightByTab.delete(tabId);
      });

      const latestAnalysis = {
        ...apiResult,
        url,
        title,
        saved_at: new Date().toISOString(),
        policy_hash: hash,
        changed_since_last: changed,
      };

      await chrome.storage.local.set({
        latestAnalysis,
        policySnapshots,
        changeLog,
      });

      // Notify the page to show the small score toast
      if (tabId >= 0) {
        chrome.tabs.sendMessage(tabId, {
          type: "DL_ANALYSIS_DONE",
          url,
          risk_score: latestAnalysis.risk_score,
          risk_level: latestAnalysis.risk_level,
          service_name: latestAnalysis.service_name,
        });
      }
      


      sendResponse({ ok: true, data: latestAnalysis });
    } catch (e: any) {
      sendResponse({ ok: false, error: String(e?.message || e) });
    }
  })();

  return true; // async
});
