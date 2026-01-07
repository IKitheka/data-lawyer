const API_BASE = "http://127.0.0.1:8000";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  
  console.log("[BG] received message:", message);

  if (message?.type !== "ANALYZE_POLICY") {
    console.warn("[BG] ignored message (wrong type)");
    return;
  }

  (async () => {
    try {
      console.log("[BG] sending request to backend...");

      const res = await fetch(`${API_BASE}/policies/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: message.url,
          text: message.text,
          service_name: message.service_name || null,
        }),
      });

      console.log("[BG] backend response status:", res.status);

      if (!res.ok) {
        const text = await res.text();
        console.error("[BG] backend error:", text);
        sendResponse({ ok: false, error: `API ${res.status}: ${text}` });
        return;
      }

      const data = await res.json();

      console.log("[BG] saving analysis to storage:", data);

      await chrome.storage.local.set({ latestAnalysis: data });

      console.log("[BG] analysis saved successfully");

      sendResponse({ ok: true, data });
    } catch (err: any) {
      console.error("[BG] exception:", err);
      sendResponse({ ok: false, error: String(err?.message || err) });
    }
  })();

  return true;
});
