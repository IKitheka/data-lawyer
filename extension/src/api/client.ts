const API_BASE = "http://127.0.0.1:8000";

export async function analyzePolicy(url: string, text: string) {
  const res = await fetch(`${API_BASE}/policies/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, text }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
