const API_BASE = "https://data-lawyer.onrender.com";
const ANALYZE_URL = `${API_BASE}/policies/analyze`;

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

async function fetchWithTimeout(input: RequestInfo, init: RequestInit = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(input, { ...init, signal: controller.signal });
    return res;
  } finally {
    clearTimeout(t);
  }
}

function explainFetchFailure(err: unknown) {
  const msg = String((err as any)?.message || err);

  // Common Chrome extension network failure
  if (msg.includes("Failed to fetch")) {
    return [
      "Failed to fetch (network/CORS/backend not reachable).",
      "Checklist:",
      "1) Is FastAPI running right now? Try: curl -i http://127.0.0.1:8000/",
      "2) If backend is on WSL/Docker/VM, 127.0.0.1 may not be the same machine as Chrome.",
      "3) If CORS is not allowed, add CORSMiddleware allow_origins=['*'] for local dev.",
      "4) Ensure host_permissions includes http://127.0.0.1:8000/* and manifest has permissions: ['storage'].",
    ].join("\n");
  }

  if (msg.includes("aborted") || msg.includes("AbortError")) {
    return `Request timed out after waiting. Is the backend slow/down? (${msg})`;
  }

  return msg;
}

// ---- main listener ----
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  // Only handle what we expect
  if (message?.type !== "ANALYZE_POLICY") return;

  (async () => {
    try {
      console.log("[BG] received message:", {
        type: message?.type,
        url: message?.url,
        title: message?.title,
        text_len: (message?.text || "").length,
      });

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

      // 5) Call backend
      console.log("[BG] sending request to backend:", ANALYZE_URL);

      let res: Response;
      try {
        res = await fetchWithTimeout(
          ANALYZE_URL,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              url,
              text,
              service_name: message.service_name || null,
            }),
          },
          20000
        );
      } catch (err) {
        const explained = explainFetchFailure(err);
        console.error("[BG] fetch error:", explained);
        sendResponse({ ok: false, error: explained });
        return;
      }

      console.log("[BG] backend response status:", res.status);

      const raw = await res.text(); // read as text first for better debugging
      if (!res.ok) {
        console.error("[BG] backend error body:", raw);
        sendResponse({ ok: false, error: `API ${res.status}: ${raw}` });
        return;
      }

      let data: any;
      try {
        data = JSON.parse(raw);
      } catch {
        console.error("[BG] backend returned non-JSON:", raw);
        sendResponse({ ok: false, error: "Backend returned non-JSON response." });
        return;
      }

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

      console.log("[BG] analysis saved successfully", { changed_since_last: changed });

      sendResponse({ ok: true, data: latestAnalysis });
    } catch (err) {
      const explained = explainFetchFailure(err);
      console.error("[BG] exception:", explained);
      sendResponse({ ok: false, error: explained });
    }
  })();

  // Tell Chrome we will respond asynchronously
  return true;
});
