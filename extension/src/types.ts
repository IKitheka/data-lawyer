export type PolicyAnalysis = {
  /* ---------- Core identifiers ---------- */
  id?: number;

  url?: string;
  domain?: string;
  service_name?: string;
  title?: string;

  /* ---------- Risk assessment ---------- */
  risk_level?: "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN" | string;
  risk_score?: number;

  summary?: string;
  red_flags?: string[];

  jurisdiction?: string;

  /* ---------- Structured analysis ---------- */
  extracted?: {
    data_collected?: Record<string, boolean>;
    data_sharing?: Record<string, boolean>;
    user_rights?: Record<string, boolean>;
    [key: string]: any;
  };

  reasoning?: {
    explanations?: string[];
    sentences_triggering_risk?: string[];
    [key: string]: any;
  };

  /* ---------- Change detection ---------- */
  policy_changed?: boolean;        // backend comparison
  changed_since_last?: boolean;    // extension snapshot logic
  policy_hash?: string;

  /* ---------- Metadata ---------- */
  saved_at?: string;

  /* ---------- LLM metadata ---------- */
  llm_model?: string;
};
