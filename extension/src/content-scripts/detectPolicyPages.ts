/* Auto-detect policy/terms pages + auto-analyze (MV3 content script) */

type AnalyzeMsg = {
  type: "ANALYZE_POLICY";
  url: string;
  text: string;
  title?: string;
  service_name?: string;
};

const MAX_CHARS = 20000;

// URL patterns that usually mean policy/terms pages
const POLICY_URL_RE =
  /(privacy|privacy-policy|terms|terms-of-service|legal|data-policy|cookies|cookie-policy|gdpr|ccpa|cpra|compliance)/i;

// Extra safety: require at least some legal/privacy keywords in page text
const KEYWORD_RE =
  /(personal data|privacy policy|terms of service|data protection|cookies|third parties|retain|retention|gdpr|ccpa|your rights|opt[- ]out|controller|processor)/i;

function getCleanText(): string {
  const root = document.body;
  if (!root) return "";
  const txt = (root.innerText || "").replace(/\s+/g, " ").trim();
  return txt.slice(0, MAX_CHARS);
}

function isLikelyPolicyPage(url: string, text: string): boolean {
  if (!POLICY_URL_RE.test(url)) return false;
  return KEYWORD_RE.test(text) || text.length > 1500;
}

async function sha256Hex(input: string): Promise<string> {
  const enc = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

let scheduled = false;

async function alreadyAnalyzed(url: string, text: string): Promise<boolean> {
  try {
    const hash = await sha256Hex(text);
    const key = `dl_auto_${new URL(url).hostname}`;
    const res = await chrome.storage.local.get([key]);
    if (res?.[key] === hash) return true;
    return false;
  } catch {
    return false;
  }
}

async function markAnalyzed(url: string, text: string): Promise<void> {
  try {
    const hash = await sha256Hex(text);
    await chrome.storage.local.set({ [`dl_auto_${new URL(url).hostname}`]: hash });
  } catch {
    // ignore
  }
}

let forceNext = false;

async function runAnalysis(force = false): Promise<void> {
  try {
    const url = location.href;
    const title = document.title || undefined;
    const text = getCleanText();

    if (force) {
      if (!text) return;
    } else {
      if (!isLikelyPolicyPage(url, text)) return;
      if (await alreadyAnalyzed(url, text)) return;
    }

    const msg: AnalyzeMsg = {
      type: "ANALYZE_POLICY",
      url,
      text,
      title,
      service_name: new URL(url).hostname,
    };

    chrome.runtime.sendMessage(msg, (res: any) => {
      if (chrome.runtime.lastError) return;
      if (res?.ok) void markAnalyzed(url, text);
    });
  } catch {
    // ignore
  }
}

function scheduleAuto(force = false): void {
  if (force) forceNext = true;
  if (scheduled) return;
  scheduled = true;

  setTimeout(async () => {
    scheduled = false;
    const f = forceNext;
    forceNext = false;
    await runAnalysis(f);
  }, force ? 0 : 1200);
}

function hookHistory(): void {
  const _pushState = history.pushState;
  const _replaceState = history.replaceState;

  history.pushState = function (...args: any[]) {
    // @ts-ignore
    _pushState.apply(this, args);
    scheduleAuto();
  } as any;

  history.replaceState = function (...args: any[]) {
    // @ts-ignore
    _replaceState.apply(this, args);
    scheduleAuto();
  } as any;

  window.addEventListener("popstate", () => scheduleAuto());
}

// Keep your existing popup flow working
chrome.runtime.onMessage.addListener((msg: any, _sender, sendResponse) => {
  if (msg?.type === "PING") {
    sendResponse({ ok: true });
    return true;
  }

  if (msg?.type === "FORCE_ANALYZE") {
    scheduleAuto(true);
    sendResponse({ ok: true });
    return true;
  }

  return false;
});

// Init
hookHistory();
if (document.readyState === "complete" || document.readyState === "interactive") {
  scheduleAuto();
} else {
  window.addEventListener("DOMContentLoaded", () => scheduleAuto(), { once: true });
}
