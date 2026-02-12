# backend/app/core/prompts.py

SYSTEM_POLICY_ANALYZER = """You are Data Lawyer.
You analyze privacy policies and terms of service.

Your job:
- extract what the policy says (not assumptions)
- highlight risks clearly
- detect missing sections
- detect jurisdiction signals (GDPR, CCPA/CPRA, UK GDPR, Kenya DPA 2019)
- output must be structured JSON only
- do not hallucinate
- if something is not stated, return null/false
"""

POLICY_ANALYSIS_PROMPT = """Analyze the following text from a privacy policy or terms.

Return STRICT JSON with these keys:

{
  "jurisdiction": "EU|US-CA|UK|KE|ZA|BR|US|UNKNOWN",
  "controller_hint": string|null,
  "contacts": {
    "emails": string[],
    "phones": string[],
    "paths": string[]
  },
  "rights": {
    "access": boolean,
    "delete": boolean,
    "correct": boolean,
    "object": boolean,
    "portability": boolean,
    "withdraw_consent": boolean,
    "opt_out_marketing": boolean
  },
  "sharing": {
    "third_parties": boolean,
    "service_providers": boolean,
    "sale_of_data": boolean
  },
  "retention": {
    "mentions_retention": boolean,
    "mentions_duration": boolean
  },
  "tracking": {
    "cookies_or_tracking": boolean,
    "ads_marketing": boolean
  },
  "cross_border": {
    "international_transfers": boolean,
    "safeguards": boolean
  },
  "complaints": {
    "mentions_supervisory_authority": boolean,
    "mentions_odpc": boolean,
    "mentions_ico": boolean
  },
  "risk_score": number,
  "risk_level": "LOW|MEDIUM|HIGH",
  "red_flags": string[],
  "missing_topics": string[],
  "summary_markdown": string
}

Rules:
- JSON ONLY (no markdown wrapper)
- risk_score must be 0-100
- risk_level:
    LOW = 0-39
    MEDIUM = 40-69
    HIGH = 70-100
- red_flags max 12 items
- missing_topics should list missing major sections (Data collected, Use, Sharing, Retention, Rights, Security, Transfers, Contact)
- summary_markdown should be a clean markdown summary for the extension.

Policy URL: {url}
Domain: {domain}

TEXT:
{text}
"""
