import { useEffect, useMemo, useState } from "react";
import type { PolicyAnalysis } from "./types";

type Stored = {
  latestAnalysis?: PolicyAnalysis;
  changeLog?: Array<{
    url: string;
    title?: string;
    changed_at: string;
    old_hash?: string;
    new_hash?: string;
  }>;
  policySnapshots?: Record<string, any>;
};

type TabKey = "overview" | "flags" | "changes";

function badgeStyle(level?: string) {
  const l = (level || "").toUpperCase();
  if (l.includes("HIGH"))
    return { background: "#ffe5e5", border: "1px solid #ffb3b3", color: "#a40000" };
  if (l.includes("MED"))
    return { background: "#fff4db", border: "1px solid #ffd08a", color: "#7a4a00" };
  if (l.includes("LOW"))
    return { background: "#e9fff0", border: "1px solid #9de7b6", color: "#0f5a2a" };
  return { background: "#eef2ff", border: "1px solid #c7d2fe", color: "#1e3a8a" };
}

function formatTime(iso?: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function clip(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function tabButton(active: boolean) {
  return {
    flex: 1,
    padding: "10px 8px",
    borderRadius: 12,
    border: "1px solid #e5e7eb",
    background: active ? "#111827" : "white",
    color: active ? "white" : "#111",
    fontSize: 12,
    cursor: "pointer",
    fontWeight: 700 as const,
  };
}

export default function App() {
  const [loading, setLoading] = useState(true);
  const [analysis, setAnalysis] = useState<PolicyAnalysis | null>(null);
  const [changeLog, setChangeLog] = useState<Stored["changeLog"]>([]);
  const [snapshotsCount, setSnapshotsCount] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("overview");
  const [busy, setBusy] = useState(false);

  const domain = useMemo(() => {
    const url = analysis?.url;
    if (!url) return null;
    try {
      return new URL(url).hostname;
    } catch {
      return url;
    }
  }, [analysis?.url]);

  const changedBanner = Boolean(
    (analysis as any)?.changed_since_last || (analysis as any)?.policy_changed
  );

  useEffect(() => {
    chrome.storage.local.get(
      ["latestAnalysis", "changeLog", "policySnapshots"],
      (res: Stored) => {
        setAnalysis(res.latestAnalysis ?? null);
        setChangeLog(res.changeLog ?? []);
        setSnapshotsCount(res.policySnapshots ? Object.keys(res.policySnapshots).length : 0);
        setLoading(false);
      }
    );

    const onChanged: Parameters<typeof chrome.storage.onChanged.addListener>[0] = (
      changes,
      area
    ) => {
      if (area !== "local") return;

      if (changes.latestAnalysis?.newValue) {
        setAnalysis(changes.latestAnalysis.newValue);
        setError(null);
      }
      if (changes.changeLog?.newValue) setChangeLog(changes.changeLog.newValue || []);
      if (changes.policySnapshots?.newValue) {
        const v = changes.policySnapshots.newValue;
        setSnapshotsCount(v ? Object.keys(v).length : 0);
      }
    };

    chrome.storage.onChanged.addListener(onChanged);
    return () => chrome.storage.onChanged.removeListener(onChanged);
  }, []);

  async function copySummary() {
    if (!analysis) return;

    const lines: string[] = [];
    lines.push("Data Lawyer Analysis");
    if (domain) lines.push(`Site: ${domain}`);
    if (analysis.risk_level) lines.push(`Risk: ${String(analysis.risk_level).toUpperCase()}`);
    if (typeof analysis.risk_score === "number") lines.push(`Score: ${analysis.risk_score}`);
    if (analysis.summary) lines.push(`Summary: ${analysis.summary}`);
    if (analysis.red_flags?.length) {
      lines.push("Red Flags:");
      for (const f of analysis.red_flags.slice(0, 8)) lines.push(`- ${f}`);
    }
    if (analysis.url) lines.push(`URL: ${analysis.url}`);

    try {
      await navigator.clipboard.writeText(lines.join("\n"));
    } catch {
      const ta = document.createElement("textarea");
      ta.value = lines.join("\n");
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
  }

  function clear() {
    chrome.storage.local.remove(["latestAnalysis"], () => {
      setAnalysis(null);
      setError(null);
    });
  }

  async function openPolicy() {
    if (!analysis?.url) return;
    chrome.tabs.create({ url: analysis.url });
  }

  // ✅ Stable “Analyze now”: inject function into the current tab to extract text,
  // then send it to background (background calls backend + saves to storage)
async function analyzeNow() {
  setBusy(true);
  setError(null);

  try {
    const [tabInfo] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tabInfo?.id) {
      setError("No active tab found.");
      setBusy(false);
      return;
    }

    chrome.tabs.sendMessage(tabInfo.id, { type: "FORCE_ANALYZE" }, (resp) => {
      const lastErr = chrome.runtime.lastError;
      if (lastErr) {
        setError(
          "Could not reach content script. Open a normal website tab (not chrome:// pages), refresh the page, then click Analyze now."
        );
        setBusy(false);
        return;
      }

      if (!resp?.ok) {
        setError(resp?.error || "Analyze failed.");
        setBusy(false);
        return;
      }

      setBusy(false);
    });
  } catch (e: any) {
    setError(String(e?.message || e));
    setBusy(false);
  }
}


  return (
    <div
      style={{
        width: 380,
        padding: 14,
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Arial",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 800, letterSpacing: 0.2 }}>Data Lawyer</div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 2 }}>
            {loading
              ? "Loading…"
              : (analysis as any)?.saved_at
              ? `Last analyzed: ${formatTime((analysis as any).saved_at)}`
              : "Last analyzed: —"}
          </div>
          <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>
            Snapshots tracked: <b>{snapshotsCount}</b>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <div style={{ fontSize: 12, padding: "6px 10px", borderRadius: 999, ...badgeStyle(analysis?.risk_level) }}>
            {analysis?.risk_level ? String(analysis.risk_level).toUpperCase() : "NO DATA"}
          </div>

          <button
            onClick={analyzeNow}
            disabled={busy}
            style={{
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid #e5e7eb",
              background: busy ? "#f3f4f6" : "white",
              color: "#111",
              fontSize: 12,
              cursor: busy ? "not-allowed" : "pointer",
              fontWeight: 700,
            }}
            title="Force analysis of the current page"
          >
            {busy ? "Analyzing…" : "Analyze now"}
          </button>
        </div>
      </div>

      {changedBanner ? (
        <div
          style={{
            marginTop: 10,
            fontSize: 12,
            padding: "10px 12px",
            borderRadius: 12,
            border: "1px solid #fecaca",
            background: "#fff1f2",
            color: "#991b1b",
            fontWeight: 650,
          }}
        >
          ⚠️ Policy changed since last time you analyzed it.
        </div>
      ) : null}

      {/* Main card */}
      <div style={{ height: 12 }} />

      <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 12, background: "white" }}>
        {loading ? (
          <div style={{ fontSize: 13, color: "#444" }}>Loading latest analysis…</div>
        ) : !analysis ? (
          <>
            <div style={{ fontSize: 13, color: "#111", fontWeight: 750 }}>No analysis yet</div>
            <div style={{ fontSize: 12, color: "#444", marginTop: 6, lineHeight: 1.45 }}>
              Open a privacy/terms page, then click <b>Analyze now</b>.
            </div>
          </>
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <div>
                <div style={{ fontSize: 13, color: "#111", fontWeight: 800 }}>
                  {analysis.service_name || domain || "Website"}
                </div>
                {analysis.url ? (
                  <div style={{ fontSize: 12, color: "#555", marginTop: 2 }}>{clip(analysis.url, 58)}</div>
                ) : null}
              </div>

              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, color: "#6b7280" }}>Score</div>
                <div style={{ fontSize: 18, fontWeight: 900, color: "#111" }}>
                  {typeof analysis.risk_score === "number" ? analysis.risk_score : "—"}
                </div>
              </div>
            </div>

            {/* Tabs */}
            <div style={{ height: 12 }} />
            <div style={{ display: "flex", gap: 8 }}>
              <button style={tabButton(tab === "overview")} onClick={() => setTab("overview")}>
                Overview
              </button>
              <button style={tabButton(tab === "flags")} onClick={() => setTab("flags")}>
                Red flags
              </button>
              <button style={tabButton(tab === "changes")} onClick={() => setTab("changes")}>
                Changes
              </button>
            </div>

            <div style={{ height: 12 }} />

            {/* Tab content */}
            {tab === "overview" ? (
              <>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#111" }}>Summary</div>
                <div style={{ fontSize: 12, color: "#333", lineHeight: 1.5, marginTop: 6 }}>
                  {analysis.summary || "No summary available yet."}
                </div>

                <div style={{ height: 10 }} />
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 11, color: "#6b7280" }}>
                    Jurisdiction: <b style={{ color: "#111" }}>{analysis.jurisdiction || "UNKNOWN"}</b>
                  </div>
                  {(analysis as any)?.llm_model ? (
                    <div style={{ fontSize: 11, color: "#6b7280" }}>
                      Model: <b style={{ color: "#111" }}>{String((analysis as any).llm_model)}</b>
                    </div>
                  ) : null}
                </div>
              </>
            ) : null}

            {tab === "flags" ? (
              <>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#111" }}>Top red flags</div>
                {analysis.red_flags?.length ? (
                  <ul
                    style={{
                      margin: "8px 0 0 18px",
                      padding: 0,
                      fontSize: 12,
                      color: "#333",
                      lineHeight: 1.55,
                    }}
                  >
                    {analysis.red_flags.slice(0, 12).map((f, i) => (
                      <li key={i} style={{ marginBottom: 6 }}>
                        {f}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div style={{ fontSize: 12, color: "#444", marginTop: 8 }}>No red flags detected.</div>
                )}
              </>
            ) : null}

            {tab === "changes" ? (
              <>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#111" }}>Recent change history</div>

                {changeLog?.length ? (
                  <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
                    {changeLog.slice(0, 6).map((c, idx) => (
                      <div
                        key={idx}
                        style={{
                          border: "1px solid #e5e7eb",
                          borderRadius: 12,
                          padding: 10,
                          background: "#fafafa",
                        }}
                      >
                        <div style={{ fontSize: 12, fontWeight: 800, color: "#111" }}>
                          {c.title || (c.url ? new URL(c.url).hostname : "Policy")}
                        </div>
                        <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
                          Changed at: <b style={{ color: "#111" }}>{formatTime(c.changed_at)}</b>
                        </div>
                        <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
                          {c.url ? clip(c.url, 60) : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: "#444", marginTop: 8 }}>
                    No changes recorded yet. When a policy changes, it will appear here.
                  </div>
                )}
              </>
            ) : null}

            {/* Errors */}
            {error ? (
              <div style={{ marginTop: 10, fontSize: 12, color: "#b91c1c", fontWeight: 650 }}>
                {error}
              </div>
            ) : null}

            {/* Actions */}
            <div style={{ height: 12 }} />
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={copySummary}
                style={{
                  flex: 1,
                  padding: "10px 10px",
                  borderRadius: 12,
                  border: "1px solid #e5e7eb",
                  background: "#111827",
                  color: "white",
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: 800,
                }}
              >
                Copy summary
              </button>

              <button
                onClick={openPolicy}
                disabled={!analysis.url}
                style={{
                  padding: "10px 10px",
                  borderRadius: 12,
                  border: "1px solid #e5e7eb",
                  background: "white",
                  color: "#111",
                  fontSize: 12,
                  cursor: analysis.url ? "pointer" : "not-allowed",
                  fontWeight: 800,
                }}
              >
                Open
              </button>

              <button
                onClick={clear}
                style={{
                  padding: "10px 10px",
                  borderRadius: 12,
                  border: "1px solid #e5e7eb",
                  background: "white",
                  color: "#111",
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: 800,
                }}
              >
                Clear
              </button>
            </div>
          </>
        )}
      </div>

      <div style={{ height: 10 }} />
      <div style={{ fontSize: 11, color: "#6b7280" }}>
        Tip: keep backend running on <b>127.0.0.1:8000</b>.
      </div>
    </div>
  );
}
