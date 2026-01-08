export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | string;

export type PolicyAnalysis = {
  url?: string;
  service_name?: string | null;
  risk_level?: RiskLevel;
  risk_score?: number;
  summary?: string;
  red_flags?: string[];
  recommendations?: string[];
  created_at?: string; // we’ll add if backend provides it; otherwise we compute in UI
};
