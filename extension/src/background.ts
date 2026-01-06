const API_BASE = "http://127.0.0.1:8000";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  // We must return true to respond asynchronously
  if (message?.type !== "ANALYZE_POLICY") return;

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/policies/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: message.url,
          text: message.text,
          service_name: message.service_name || null,
        }),
      });

      if (!res.ok) {
        const text = await res.text();
        sendResponse({ ok: false, error: `API ${res.status}: ${text}` });
        return;
      }

      const data = await res.json();

      // Save result for popup
      await chrome.storage.local.set({ latestAnalysis: data });

      sendResponse({ ok: true, data });
    } catch (err: any) {
      sendResponse({ ok: false, error: String(err?.message || err) });
    }
  })();

  return true;
});
