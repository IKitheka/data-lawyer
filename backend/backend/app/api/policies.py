from fastapi import APIRouter, Request, Depends
from backend.app.core.auth import require_api_key
from backend.app.core.limiter import limiter, tier_limit_provider
import re

router = APIRouter()

RED_FLAG_RULES = [
    ("share", r"\bshare\b|\bshared\b|\bsharing\b"),
    ("sell", r"\bsell\b|\bsold\b|\bselling\b"),
    ("third parties", r"\bthird[- ]party\b|\bthird parties\b"),
    ("advertising", r"\badvertis(ing|ers|ement)\b|\btargeted ads?\b"),
    ("tracking", r"\btrack(ing|ers|ed)?\b|\bcookies\b|\bpixels?\b"),
    ("location", r"\blocation\b|\bgps\b|\bprecise location\b"),
    ("biometric", r"\bbiometric\b|\bface\b|\bfingerprint\b"),
    ("retain", r"\bretain\b|\bretention\b|\bstorage period\b"),
    ("law enforcement", r"\blaw enforcement\b|\bgovernment request\b|\bsubpoena\b"),
    ("international transfer", r"\binternational\b|\bcross[- ]border\b|\btransfer\b"),
]

def guess_jurisdiction(text: str) -> str:
    t = text.lower()
    if "gdpr" in t or "eea" in t or "european union" in t:
        return "EU"
    if "california" in t or "ccpa" in t or "cpra" in t:
        return "US-CA"
    if "united states" in t:
        return "US"
    if "kenya" in t or "data protection act" in t:
        return "KE"
    return "UNKNOWN"

def summarize(text: str, max_sentences: int = 3) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "No readable policy text found on this page."
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    picked = []
    for s in sentences:
        s = s.strip()
        if len(s) < 40:
            continue
        picked.append(s)
        if len(picked) >= max_sentences:
            break
    return " ".join(picked) if picked else "Policy text detected, but could not extract clear sentences."

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")

    t = text.lower()
    flags = [label for (label, pattern) in RED_FLAG_RULES if re.search(pattern, t)]

    score = min(100, len(flags) * 12)
    if score >= 60:
        level = "HIGH"
    elif score >= 25:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summarize(text),
        "red_flags": flags[:12],
        "jurisdiction": guess_jurisdiction(text),
        "service_name": url,
        "llm_model": "mvp-rules-v1",
    }
