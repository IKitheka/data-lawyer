function isPolicyPage(url: string, text: string) {
  const u = url.toLowerCase();
  const t = text.toLowerCase();

  return (
    u.includes("privacy") ||
    u.includes("terms") ||
    u.includes("policy") ||
    t.includes("privacy policy") ||
    t.includes("terms of service")
  );
}
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "FORCE_ANALYZE") return;

  (async () => {
    try {
      await run(); // run() is your existing function that detects/analyzes
      sendResponse({ ok: true });
    } catch (e: any) {
      sendResponse({ ok: false, error: String(e?.message || e) });
    }
  })();

  return true;
});


async function run(force = false) {
  const url = window.location.href;
  const text = document.body.innerText.slice(0, 20000);

  // If not forced, only run on policy-like pages
  if (!force && !isPolicyPage(url, text)) return;

  chrome.runtime.sendMessage(
    { type: "ANALYZE_POLICY", url, text, title: document.title },
    (resp) => {
      if (chrome.runtime.lastError) {
        console.error("[Data Lawyer] runtime message error:", chrome.runtime.lastError.message);
        return;
      }
      if (!resp?.ok) {
        console.error("[Data Lawyer] Analyze failed:", resp?.error);
        return;
      }
      console.log("[Data Lawyer] Analysis saved via background:", resp.data);
    }
  );
}

// Run automatically when page loads (for policy pages)
run(false);

// ✅ Listen for popup "Analyze now"
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "FORCE_ANALYZE") return;

  run(true)
    .then(() => sendResponse({ ok: true }))
    .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));

  return true; // async response
});
