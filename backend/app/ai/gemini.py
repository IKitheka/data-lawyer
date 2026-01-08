import os
import json
from dotenv import load_dotenv
from google import genai

def analyze_with_gemini(cleaned_text: str, url: str, domain: str) -> dict:
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set. Put it in backend/.env")

    client = genai.Client(api_key=api_key)

    prompt = f"""
You are a privacy lawyer.

Analyze this privacy policy and return STRICT JSON only.

Return JSON with:
- summary: 2-3 sentence summary (plain English, user-friendly)
- red_flags: array of short bullet points (strings)
- risk_level: one of ["LOW","MEDIUM","HIGH"]

Context:
url: {url}
domain: {domain}

Policy text:
\"\"\"
{cleaned_text[:12000]}
\"\"\"

IMPORTANT:
- Output MUST be valid JSON only.
- No markdown, no backticks, no extra words.
"""

    # We'll start with this. If your key doesn't support it, we'll list models.
    model_name = "models/gemini-2.5-flash"

    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
    )

    raw = (resp.text or "").strip()

    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(raw[start:end+1])
        else:
            raise RuntimeError(f"Gemini returned non-JSON output: {raw[:300]}")

    summary = str(data.get("summary", "")).strip()
    red_flags = data.get("red_flags", [])
    if not isinstance(red_flags, list):
        red_flags = []
    red_flags = [str(x).strip() for x in red_flags if str(x).strip()]

    risk_level = str(data.get("risk_level", "")).strip().upper()
    if risk_level not in {"LOW", "MEDIUM", "HIGH"}:
        risk_level = ""

    return {"summary": summary, "red_flags": red_flags, "risk_level": risk_level}
