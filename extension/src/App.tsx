import { useEffect, useMemo, useState } from "react";
import type { PolicyAnalysis } from "./types";

type Stored = { latestAnalysis?: PolicyAnalysis & { saved_at?: string } };

function badgeStyle(level?: string) {
  const l = (level || "").toUpperCase();
  if (l.includes("HIGH")) return { background: "#ffe5e5", border: "1px solid #ffb3b3", color: "#a40000" };
  if (l.includes("MED")) return { background: "#fff4db", border: "1px solid #ffd08a", color: "#7a4a00" };
  if (l.includes("LOW")) return { background: "#e9fff0", border: "1px solid #9de7b6", color: "#0f5a2a" };
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

export default function App() {
  const [loading, setLoading] = useState(true);
  const [analysis, setAnalysis] = useState<(PolicyAnalysis & { saved_at?: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  const domain = useMemo(() => {
    const url = analysis?.url;
    if (!url) return null;
    try {
      return new URL(url).hostname;
    } catch {
      return url;
    }
  }, [analysis?.url]);

  useEffect(() => {
    // Initial load
    chrome.storage.local.get(["latestAnalysis"], (res: Stored) => {
      setAnalysis(res.latestAnalysis ?? null);
      setLoading(false);
    });

    // Live updates whenever background saves a new analysis
    const onChanged: Parameters<typeof chrome.storage.onChanged.addListener>[0] = (changes, area) => {
      if (area !== "local") return;
      if (changes.latestAnalysis?.newValue) {
        setAnalysis(changes.latestAnalysis.newValue);
        setError(null);
      }
    };

    chrome.storage.onChanged.addListener(onChanged);
    return () => chrome.storage.onChanged.removeListener(onChanged);
  }, []);

  async function copySummary() {
    if (!analysis) return;
    const lines: string[] = [];
    lines.push(`Data Lawyer Analysis`);
    if (domain) lines.push(`Site: ${domain}`);
    if (analysis.risk_level) lines.push(`Risk: ${analysis.risk_level}`);
    if (typeof analysis.risk_score === "number") lines.push(`Score: ${analysis.risk_score}`);
    if (analysis.summary) lines.push(`Summary: ${analysis.summary}`);
    if (analysis.red_flags?.length) {
      lines.push(`Red Flags:`);
      for (const f of analysis.red_flags.slice(0, 8)) lines.push(`- ${f}`);
    }
    if (analysis.url) lines.push(`URL: ${analysis.url}`);

    try {
      await navigator.clipboard.writeText(lines.join("\n"));
    } catch {
      // Fallback for clipboard permission quirks
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

  return (
    <div style={{ width: 360, padding: 14, fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Arial" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Data Lawyer</div>
          <div style={{ fontSize: 12, color: "#555" }}>
            {loading ? "Loading…" : analysis?.saved_at ? `Last analyzed: ${formatTime(analysis.saved_at)}` : "Last analyzed: —"}
          </div>
        </div>
        <div style={{ fontSize: 12, padding: "6px 10px", borderRadius: 999, ...badgeStyle(analysis?.risk_level) }}>
          {analysis?.risk_level ? String(analysis.risk_level).toUpperCase() : "NO DATA"}
        </div>
      </div>

      <div style={{ height: 10 }} />

      <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "white" }}>
        {loading ? (
          <div style={{ fontSize: 13, color: "#444" }}>Loading latest analysis…</div>
        ) : !analysis ? (
          <div style={{ fontSize: 13, color: "#444" }}>
            No analysis yet. Visit a privacy policy page (e.g. contains <b>privacy</b> / <b>terms</b>) and refresh.
          </div>
        ) : (
          <>
            <div style={{ fontSize: 13, color: "#111", fontWeight: 650 }}>
              {analysis.service_name || domain || "Website"}
            </div>
            {analysis.url ? (
              <div style={{ fontSize: 12, color: "#555", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {analysis.url}
              </div>
            ) : null}

            <div style={{ height: 10 }} />

            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <div style={{ fontSize: 12, color: "#555" }}>Score</div>
              <div style={{ fontSize: 14, fontWeight: 800 }}>
                {typeof analysis.risk_score === "number" ? analysis.risk_score : "—"}
              </div>
            </div>

            {analysis.summary ? (
              <>
                <div style={{ height: 10 }} />
                <div style={{ fontSize: 12, fontWeight: 700, color: "#111" }}>Summary</div>
                <div style={{ fontSize: 12, color: "#333", lineHeight: 1.4 }}>{analysis.summary}</div>
              </>
            ) : null}

            {analysis.red_flags?.length ? (
              <>
                <div style={{ height: 10 }} />
                <div style={{ fontSize: 12, fontWeight: 700, color: "#111" }}>Red flags</div>
                <ul style={{ margin: "6px 0 0 18px", padding: 0, fontSize: 12, color: "#333" }}>
                  {analysis.red_flags.slice(0, 6).map((f:string, i: number) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      {f}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            {error ? (
              <>
                <div style={{ height: 10 }} />
                <div style={{ fontSize: 12, color: "#b91c1c" }}>{error}</div>
              </>
            ) : null}

            <div style={{ height: 12 }} />

            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={copySummary}
                style={{
                  flex: 1,
                  padding: "10px 10px",
                  borderRadius: 10,
                  border: "1px solid #e5e7eb",
                  background: "#111827",
                  color: "white",
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Copy summary
              </button>
              <button
                onClick={clear}
                style={{
                  padding: "10px 10px",
                  borderRadius: 10,
                  border: "1px solid #e5e7eb",
                  background: "white",
                  color: "#111",
                  fontSize: 12,
                  cursor: "pointer",
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
        Tip: keep your backend running on <b>127.0.0.1:8000</b>.
      </div>
    </div>
  );
}
