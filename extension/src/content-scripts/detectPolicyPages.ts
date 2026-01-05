import { analyzePolicy } from "../api/client";

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

async function run() {
  const url = window.location.href;
  const text = document.body.innerText.slice(0, 20000);

  if (!isPolicyPage(url, text)) return;

  try {
    const result = await analyzePolicy(url, text);
    // Save latest analysis for popup to display
    chrome.storage.local.set({ latestAnalysis: result });
    console.log("[Data Lawyer] Saved analysis:", result);
  } catch (e) {
    console.error("[Data Lawyer] Failed:", e);
  }
}

run();
