import { useEffect, useState } from "react";

type Analysis = {
  service_name: string;
  risk_level: string;
  risk_score: number;
  jurisdiction: string;
  summary: string;
  red_flags: string[];
  policy_changed: boolean;
};

export default function App() {
  const [data, setData] = useState<Analysis | null>(null);

  useEffect(() => {
  chrome.storage.local.get(["latestAnalysis"], (res: { latestAnalysis?: Analysis }) => {
    if (res.latestAnalysis) setData(res.latestAnalysis);
  });
}, []);


  return (
    <div style={{ width: 340, padding: 12, fontFamily: "system-ui, sans-serif" }}>
      <h2 style={{ margin: 0 }}>Data Lawyer</h2>
      <p style={{ marginTop: 6, color: "#555" }}>
        Visit a privacy policy page and reopen this popup.
      </p>

      {!data ? (
        <div style={{ marginTop: 12, padding: 10, border: "1px solid #ddd", borderRadius: 8 }}>
          No analysis yet.
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <div style={{ padding: 10, border: "1px solid #ddd", borderRadius: 8 }}>
            <div><b>Service:</b> {data.service_name}</div>
            <div><b>Risk:</b> {data.risk_level} ({Math.round(data.risk_score)}/100)</div>
            <div><b>Law:</b> {data.jurisdiction}</div>
            {data.policy_changed && (
              <div style={{ marginTop: 6, fontWeight: 700 }}>
                ⚠ Policy changed since last check
              </div>
            )}
          </div>

          <div style={{ marginTop: 10 }}>
            <b>Summary</b>
            <div style={{ marginTop: 6, padding: 10, border: "1px solid #eee", borderRadius: 8 }}>
              {data.summary}
            </div>
          </div>

          <div style={{ marginTop: 10 }}>
            <b>Red flags</b>
            <ul style={{ marginTop: 6 }}>
              {data.red_flags.length ? data.red_flags.map((x, i) => <li key={i}>{x}</li>) : <li>None detected.</li>}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
