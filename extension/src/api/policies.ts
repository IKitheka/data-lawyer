import { apiFetch } from "./client";

export type AnalyzePayload = {
  url?: string;
  text: string;
  service_name?: string;
};

export async function analyzePolicy(payload: AnalyzePayload, signal?: AbortSignal) {
  const res = await apiFetch("/policies/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.error || err?.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
