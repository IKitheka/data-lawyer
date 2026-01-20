function isPolicyPage(url: string, text: string) {
  const u = (url || "").toLowerCase();
  const t = (text || "").toLowerCase();
  return (
    u.includes("privacy") ||
    u.includes("terms") ||
    u.includes("policy") ||
    t.includes("privacy policy") ||
    t.includes("terms of service")
  );
}

const COOLDOWN_MS = 5 * 60 * 1000; // 5 minutes
const LAST_RUN_KEY = "dl_last_run_ts";

async function getLastRun(): Promise<number> {
  try {
    const res = await chrome.storage.local.get([LAST_RUN_KEY]);
    const v = res?.[LAST_RUN_KEY];
    return typeof v === "number" ? v : 0;
  } catch {
    return 0;
  }
}

async function setLastRun(ts: number) {
  try {
    await chrome.storage.local.set({ [LAST_RUN_KEY]: ts });
  } catch {}
}

async function run(force = false) {
  const url = window.location.href;
  const text = (document.body?.innerText || "").slice(0, 20000);

  if (!force && !isPolicyPage(url, text)) return;

  const now = Date.now();
  const last = await getLastRun();
  if (!force && now - last < COOLDOWN_MS) {
    console.log("[Data Lawyer] cooldown: skipping auto analyze");
    return;
  }
  await setLastRun(now);

  chrome.runtime.sendMessage(
    { type: "ANALYZE_POLICY", url, text, title: document.title },
    (resp) => {
      if (chrome.runtime.lastError) {
        console.error("[Data Lawyer] runtime error:", chrome.runtime.lastError.message);
        return;
      }
      if (!resp?.ok) {
        console.error("[Data Lawyer] Analyze failed:", resp?.error);
        return;
      }
      console.log("[Data Lawyer] Analysis saved:", resp.data);
    }
  );
}

run(false);

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "PING") {
    sendResponse({ ok: true });
    return;
  }

  if (msg?.type === "FORCE_ANALYZE") {
    run(true)
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));
    return true;
  }
});
