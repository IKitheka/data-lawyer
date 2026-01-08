const API_BASE = "http://127.0.0.1:8000";

async function sha256(text: string): Promise<string> {
  const enc = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function keyForUrl(url: string) {
  // simple stable key for storage
  return url.trim().toLowerCase();
}


chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  
  console.log("[BG] received message:", message);

  if (message?.type !== "ANALYZE_POLICY") {
  console.warn("[BG] ignored message (wrong type)");
  sendResponse?.({ ok: false, error: "Ignored message type" });
  return false;
}


  (async () => {
  try {
    const url: string = message.url;
    const text: string = message.text || "";
    const title: string | undefined = message.title;

    // ✅ 1) Hash the policy text
    const urlKey = keyForUrl(url);
    const newHash = await sha256(text);

    // ✅ 2) Load previous snapshot + changelog
    const stored = await chrome.storage.local.get(["policySnapshots", "changeLog"]);
    const policySnapshots: Record<string, any> = stored.policySnapshots || {};
    const changeLog: any[] = stored.changeLog || [];

    const prev = policySnapshots[urlKey];
    const changed = !!prev && prev.last_hash !== newHash;

    // ✅ 3) If changed, log an event
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

    // ✅ 4) Update snapshot for next time
    policySnapshots[urlKey] = {
      last_hash: newHash,
      last_seen: new Date().toISOString(),
      last_url: url,
      last_title: title,
    };

    // ✅ 5) Now call backend (same as before)
    console.log("[BG] sending request to backend...");
    const res = await fetch(`${API_BASE}/policies/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        text,
        service_name: message.service_name || null,
      }),
    });

    console.log("[BG] backend response status:", res.status);

    if (!res.ok) {
      const errText = await res.text();
      console.error("[BG] backend error:", errText);
      sendResponse({ ok: false, error: `API ${res.status}: ${errText}` });
      return;
    }

    const data = await res.json();

    // ✅ 6) Save latest analysis + change status for popup
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
  } catch (err: any) {
    console.error("[BG] exception:", err);
    sendResponse({ ok: false, error: String(err?.message || err) });
  }
})();


  return true;
});
