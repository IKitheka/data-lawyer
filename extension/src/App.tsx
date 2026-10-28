import { useEffect, useMemo, useState } from "react";
import type { PolicyAnalysis } from "./types";
import ReactMarkdown from "react-markdown";
import { jsPDF } from "jspdf";




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

const UI = {
  bg: "#0b1220",
  card: "#0f172a",
  card2: "#0b1324",
  border: "rgba(255,255,255,0.08)",
  text: "#e5e7eb",
  muted: "rgba(229,231,235,0.72)",
  faint: "rgba(229,231,235,0.55)",
  good: "#22c55e",
  warn: "#f59e0b",
  bad: "#ef4444",
  blue: "#60a5fa",
};

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

function riskColor(level?: string) {
  const l = (level || "").toUpperCase();
  if (l.includes("HIGH")) return UI.bad;
  if (l.includes("MED")) return UI.warn;
  if (l.includes("LOW")) return UI.good;
  return UI.blue;
}

function badge(level?: string) {
  const c = riskColor(level);
  return {
    background: `linear-gradient(180deg, rgba(255,255,255,0.10), rgba(255,255,255,0.02))`,
    border: `1px solid ${c}`,              // ✅ use c here
    boxShadow: `0 0 0 3px rgba(255,255,255,0.06)`,
    color: UI.text,
    padding: "6px 10px",
    borderRadius: 999,
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12,
    fontWeight: 800 as const,
    letterSpacing: 0.4,
  };
}


function pillDot(color: string) {
  return {
    width: 9,
    height: 9,
    borderRadius: 999,
    background: color,
    boxShadow: `0 0 0 3px rgba(255,255,255,0.06)`,
  };
}

function tabStyle(active: boolean) {
  return {
    flex: 1,
    padding: "10px 10px",
    borderRadius: 12,
    border: `1px solid ${UI.border}`,
    background: active ? "rgba(255,255,255,0.10)" : "rgba(255,255,255,0.04)",
    color: active ? UI.text : UI.muted,
    fontSize: 12,
    cursor: "pointer",
    fontWeight: 800 as const,
    transition: "all 120ms ease",
  };
}

function buttonPrimary(disabled?: boolean) {
  return {
    padding: "10px 12px",
    borderRadius: 12,
    border: `1px solid ${UI.border}`,
    background: disabled
      ? "rgba(255,255,255,0.06)"
      : "linear-gradient(180deg, rgba(96,165,250,0.40), rgba(96,165,250,0.18))",
    color: disabled ? UI.faint : UI.text,
    fontSize: 12,
    cursor: disabled ? "not-allowed" : "pointer",
    fontWeight: 900 as const,
    letterSpacing: 0.2,
  };
}

function buttonGhost(disabled?: boolean) {
  return {
    padding: "10px 12px",
    borderRadius: 12,
    border: `1px solid ${UI.border}`,
    background: "rgba(255,255,255,0.04)",
    color: disabled ? UI.faint : UI.text,
    fontSize: 12,
    cursor: disabled ? "not-allowed" : "pointer",
    fontWeight: 800 as const,
  };
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11, color: UI.muted }}>
      <div>{label}</div>
      <div style={{ color: UI.text, fontWeight: 800, textAlign: "right" }}>{value}</div>
    </div>
  );
}

function ScoreMeter({ score, level }: { score?: number; level?: string }) {
  const s = typeof score === "number" ? Math.max(0, Math.min(100, score)) : 0;
  const c = riskColor(level);
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: UI.muted }}>
        <span>Score</span>
        <span style={{ color: UI.text, fontWeight: 900 }}>{typeof score === "number" ? score : "—"}</span>
      </div>
      <div
        style={{
          marginTop: 8,
          height: 10,
          borderRadius: 999,
          background: "rgba(255,255,255,0.06)",
          border: `1px solid ${UI.border}`,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${s}%`,
            height: "100%",
            background: `linear-gradient(90deg, ${c}, rgba(255,255,255,0.10))`,
          }}
        />
      </div>
    </div>
  );
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

  const changedBanner = Boolean((analysis as any)?.changed_since_last || (analysis as any)?.policy_changed);

  useEffect(() => {
    chrome.storage.local.get(["latestAnalysis", "changeLog", "policySnapshots"], (res: Stored) => {
      setAnalysis(res.latestAnalysis ?? null);
      setChangeLog(res.changeLog ?? []);
      setSnapshotsCount(res.policySnapshots ? Object.keys(res.policySnapshots).length : 0);
      setLoading(false);
    });

    const onChanged: Parameters<typeof chrome.storage.onChanged.addListener>[0] = (changes, area) => {
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
      for (const f of analysis.red_flags.slice(0, 10)) lines.push(`- ${f}`);
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

function mdToText(md: string) {
  return (md || "")
    // remove markdown headings
    .replace(/^#{1,6}\s+/gm, "")
    // bullets
    .replace(/^\s*[-*+]\s+/gm, "• ")
    // numbered list
    .replace(/^\s*\d+\.\s+/gm, "• ")
    // bold/italics/code
    .replace(/[`*_]/g, "")
    // links [text](url) -> text (url)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .trim();
}

function safeFilename(s: string) {
  return (s || "data-lawyer-report")
    .toLowerCase()
    .replace(/https?:\/\//g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function exportPdf() {
  if (!analysis) return;

  const doc = new jsPDF({ unit: "pt", format: "a4" });

  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 40;
  let y = margin;

  const title = "Data Lawyer Report";
  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.text(title, margin, y);
  y += 20;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  const generatedAt =
    (analysis as any)?.saved_at ? String((analysis as any).saved_at) : new Date().toISOString();
  doc.text(`Generated: ${generatedAt}`, margin, y);
  y += 18;

  // Divider
  doc.setLineWidth(0.5);
  doc.line(margin, y, pageW - margin, y);
  y += 14;

  const addBlock = (label: string, value: string) => {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(11);
    doc.text(label, margin, y);
    y += 14;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(10);

    const lines = doc.splitTextToSize(value || "—", pageW - margin * 2);
    for (const line of lines) {
      if (y > pageH - margin) {
        doc.addPage();
        y = margin;
      }
      doc.text(line, margin, y);
      y += 14;
    }
    y += 8;
  };

  addBlock("Website", String(analysis.service_name || "—"));
  addBlock("URL", String(analysis.url || "—"));
  addBlock("Risk", `${String(analysis.risk_level || "UNKNOWN").toUpperCase()}  |  Score: ${analysis.risk_score ?? "—"}`);
  addBlock("Jurisdiction", String(analysis.jurisdiction || "UNKNOWN"));

  if (analysis.red_flags?.length) {
    addBlock(
      "Top red flags",
      analysis.red_flags.slice(0, 12).map((f) => `• ${f}`).join("\n")
    );
  } else {
    addBlock("Top red flags", "None detected.");
  }

  addBlock("Summary", mdToText(String(analysis.summary || "No summary available.")));

  // footer on each page (simple)
  const totalPages = doc.getNumberOfPages();
  for (let i = 1; i <= totalPages; i++) {
    doc.setPage(i);
    doc.setFontSize(9);
    doc.setTextColor(120);
    doc.text(
      `Data Lawyer • Page ${i} of ${totalPages}`,
      margin,
      pageH - 18
    );
  }

  const domainOrName = String(analysis.service_name || analysis.url || "report");
  doc.save(`${safeFilename(domainOrName)}-data-lawyer-report.pdf`);
}


  function clear() {
    chrome.storage.local.remove(["latestAnalysis"], () => {
      setAnalysis(null);
      setError(null);
    });
  }

  function openPolicy() {
    if (!analysis?.url) return;
    chrome.tabs.create({ url: analysis.url });
  }

  // ✅ No chrome.scripting here (avoids executeScript undefined errors).
  // We just message the already-injected content script.
  async function analyzeNow() {
  setBusy(true);
  setError(null);

  try {
    const [t] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!t?.id) {
      setError("No active tab found.");
      return;
    }

    if (!t.url || !/^https?:\/\//i.test(t.url)) {
      setError("Open a normal website tab (http/https), then try again.");
      return;
    }

    // PING
    const has = await new Promise<boolean>((resolve) => {
      chrome.tabs.sendMessage(t.id!, { type: "PING" }, (resp) => {
        if (chrome.runtime.lastError) return resolve(false);
        resolve(!!resp?.ok);
      });
    });

    if (!has) {
      setError("Content script not reachable. Reload the tab once (Ctrl+R) and try again.");
      return;
    }

    // FORCE ANALYZE
    chrome.tabs.sendMessage(t.id!, { type: "FORCE_ANALYZE" }, (resp) => {
      if (chrome.runtime.lastError) {
        setError("Could not reach the page script. Reload the page once, then try again.");
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
  } finally {
    setBusy(false);
  }
}






  const level = analysis?.risk_level ? String(analysis.risk_level).toUpperCase() : "NO DATA";
  const levelDot = riskColor(level);

  return (
    <div
      style={{
        width: 390,
        padding: 14,
        background: `radial-gradient(1000px 260px at 40% 0%, rgba(96,165,250,0.25), transparent 60%),
                     radial-gradient(900px 260px at 90% 0%, rgba(239,68,68,0.18), transparent 60%),
                     ${UI.bg}`,
        color: UI.text,
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Arial",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 950, letterSpacing: 0.3 }}>Data Lawyer</div>
          <div style={{ marginTop: 4, fontSize: 12, color: UI.muted }}>
            {loading
              ? "Loading…"
              : (analysis as any)?.saved_at
              ? `Last analyzed: ${formatTime((analysis as any).saved_at)}`
              : "Last analyzed: —"}
          </div>
          <div style={{ marginTop: 4, fontSize: 11, color: UI.faint }}>
            Snapshots tracked: <span style={{ color: UI.text, fontWeight: 900 }}>{snapshotsCount}</span>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <div style={badge(level)}>
            <span style={pillDot(levelDot)} />
            {level}
          </div>

          <button onClick={analyzeNow} disabled={busy} style={buttonPrimary(busy)} title="Analyze the current page">
            {busy ? "Analyzing…" : "Analyze now"}
          </button>
        </div>
      </div>

      {/* Change banner */}
      {changedBanner ? (
        <div
          style={{
            marginTop: 12,
            borderRadius: 14,
            border: `1px solid rgba(239,68,68,0.35)`,
            background: "linear-gradient(180deg, rgba(239,68,68,0.15), rgba(239,68,68,0.06))",
            padding: "10px 12px",
            fontSize: 12,
            fontWeight: 850,
          }}
        >
          ⚠️ This policy changed since the last time you analyzed it.
        </div>
      ) : null}

      {/* Main card */}
      <div style={{ height: 12 }} />
      <div
        style={{
          border: `1px solid ${UI.border}`,
          borderRadius: 16,
          padding: 12,
          background: `linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))`,
          boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
        }}
      >
        {loading ? (
          <div style={{ fontSize: 13, color: UI.muted }}>Loading latest analysis…</div>
        ) : !analysis ? (
          <>
            <div style={{ fontSize: 13, fontWeight: 950 }}>No analysis yet</div>
            <div style={{ marginTop: 8, fontSize: 12, color: UI.muted, lineHeight: 1.5 }}>
              Open a privacy/terms page, then click <b>Analyze now</b>.
            </div>
            {error ? (
              <div
                style={{
                  marginTop: 10,
                  borderRadius: 14,
                  border: `1px solid rgba(239,68,68,0.35)`,
                  background: "rgba(239,68,68,0.10)",
                  padding: "10px 12px",
                  fontSize: 12,
                  color: UI.text,
                  fontWeight: 800,
                }}
              >
                {error}
              </div>
            ) : null}
          </>
        ) : (
          <>
            {/* Title / URL */}
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 950 }}>
                  {analysis.service_name || domain || "Website"}
                </div>
                {analysis.url ? (
                  <div style={{ marginTop: 4, fontSize: 12, color: UI.muted }}>
                    {clip(analysis.url, 64)}
                  </div>
                ) : null}
              </div>
            </div>

            {/* Score meter */}
            <ScoreMeter score={analysis.risk_score} level={analysis.risk_level} />

            <div style={{ height: 12 }} />

            {/* Tabs */}
            <div style={{ display: "flex", gap: 8 }}>
              <button style={tabStyle(tab === "overview")} onClick={() => setTab("overview")}>
                Overview
              </button>
              <button style={tabStyle(tab === "flags")} onClick={() => setTab("flags")}>
                Red flags
              </button>
              <button style={tabStyle(tab === "changes")} onClick={() => setTab("changes")}>
                Changes
              </button>
            </div>

            <div style={{ height: 12 }} />

            {/* Tab content */}
            <div
              style={{
                borderRadius: 14,
                border: `1px solid ${UI.border}`,
                background: "rgba(0,0,0,0.18)",
                padding: 12,
              }}
            >
              {tab === "overview" ? (
                <>
                  <div style={{ fontSize: 12, fontWeight: 950 }}>Summary</div>
                  <div style={{ marginTop: 8, color: UI.text }}>
                    {analysis.summary ? (
                     <ReactMarkdown
                       components={{
                         h2: ({ children }) => (
                           <div style={{ fontSize: 13, fontWeight: 950, margin: "6px 0 8px" }}>{children}</div>
                         ),
                         h3: ({ children }) => (
                           <div style={{ fontSize: 12, fontWeight: 900, margin: "10px 0 6px", color: UI.text }}>{children}</div>
                         ),
                         p: ({ children }) => (
                           <div style={{ fontSize: 12, lineHeight: 1.55, color: UI.muted, margin: "6px 0" }}>{children}</div>
                         ),
                         ul: ({ children }) => (
                           <ul style={{ margin: "6px 0 8px 18px", padding: 0, fontSize: 12, lineHeight: 1.55, color: UI.muted }}>
                             {children}
                         </ul>
                         ),
                         li: ({ children }) => <li style={{ marginBottom: 6 }}>{children}</li>,
                         strong: ({ children }) => <span style={{ color: UI.text, fontWeight: 900 }}>{children}</span>,
                       }}
                      >
                       {analysis.summary}
                      </ReactMarkdown>
                    ) : (
                      <div style={{ fontSize: 12, color: UI.muted, lineHeight: 1.55 }}>No summary available yet.</div>
                    )}
                  </div>


                  <div style={{ height: 10 }} />
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <InfoRow label="Jurisdiction" value={analysis.jurisdiction || "UNKNOWN"} />
                    {(analysis as any)?.llm_model ? (
                      <InfoRow label="Analysis model" value={String((analysis as any).llm_model)} />
                    ) : null}
                  </div>
                </>
              ) : null}

              {tab === "flags" ? (
                <>
                  <div style={{ fontSize: 12, fontWeight: 950 }}>Top red flags</div>
                  {analysis.red_flags?.length ? (
                    <ul style={{ margin: "10px 0 0 18px", padding: 0, fontSize: 12, color: UI.text, lineHeight: 1.55 }}>
                      {analysis.red_flags.slice(0, 12).map((f, i) => (
                        <li key={i} style={{ marginBottom: 8, color: UI.text }}>
                          <span style={{ color: UI.muted }}>{f}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div style={{ marginTop: 10, fontSize: 12, color: UI.muted }}>No red flags detected.</div>
                  )}
                </>
              ) : null}

              {tab === "changes" ? (
                <>
                  <div style={{ fontSize: 12, fontWeight: 950 }}>Recent change history</div>
                  {changeLog?.length ? (
                    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
                      {changeLog.slice(0, 6).map((c, idx) => (
                        <div
                          key={idx}
                          style={{
                            border: `1px solid ${UI.border}`,
                            borderRadius: 14,
                            padding: 10,
                            background: "rgba(255,255,255,0.04)",
                          }}
                        >
                          <div style={{ fontSize: 12, fontWeight: 950 }}>
                            {c.title || (c.url ? new URL(c.url).hostname : "Policy")}
                          </div>
                          <div style={{ marginTop: 4, fontSize: 11, color: UI.muted }}>
                            Changed at: <span style={{ color: UI.text, fontWeight: 900 }}>{formatTime(c.changed_at)}</span>
                          </div>
                          <div style={{ marginTop: 4, fontSize: 11, color: UI.muted }}>{c.url ? clip(c.url, 62) : ""}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ marginTop: 10, fontSize: 12, color: UI.muted }}>
                      No changes recorded yet. When a policy changes, it will appear here.
                    </div>
                  )}
                </>
              ) : null}
            </div>

            {/* Error */}
            {error ? (
              <div
                style={{
                  marginTop: 10,
                  borderRadius: 14,
                  border: `1px solid rgba(239,68,68,0.35)`,
                  background: "rgba(239,68,68,0.10)",
                  padding: "10px 12px",
                  fontSize: 12,
                  color: UI.text,
                  fontWeight: 800,
                }}
              >
                {error}
              </div>
            ) : null}

            {/* Actions */}
            <div style={{ height: 12 }} />
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={copySummary} style={{ ...buttonPrimary(false), flex: 1 }}>
                Copy summary
              </button>
             
              <button onClick={exportPdf} disabled={!analysis} style={buttonGhost(!analysis)}>
                Export PDF
              </button>
   

              <button onClick={openPolicy} disabled={!analysis.url} style={buttonGhost(!analysis.url)}>
                Open
              </button>

              <button onClick={clear} style={buttonGhost(false)}>
                Clear
              </button>
            </div>
          </>
        )}
      </div>

      <div style={{ height: 10 }} />
      <div style={{ fontSize: 11, color: UI.faint }}>
        Tip: using the production API at <b style={{ color: UI.text }}>data-lawyer.onrender.com</b>.
      </div>
    </div>
  );
}

