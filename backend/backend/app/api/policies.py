from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

# ---------- Heuristics (no LLM) ----------

def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or url
    except Exception:
        return url

def _detect_jurisdiction(text: str) -> str:
    t = text.lower()
    # Very basic signals (extend later)
    if "gdpr" in t or "european union" in t or "eea" in t:
        return "EU"
    if "california consumer privacy act" in t or "ccpa" in t or "cpra" in t:
        return "US-CA"
    if "united states" in t or "u.s." in t or "usa" in t:
        return "US"
    if "united kingdom" in t or "uk gdpr" in t:
        return "UK"
    if "kenya" in t or "data protection act, 2019" in t:
        return "KE"
    if "south africa" in t or "popia" in t:
        return "ZA"
    return "UNKNOWN"

def _find_sentences(text: str, keywords: list[str], max_items: int = 4) -> list[str]:
    # Split into sentence-ish chunks
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    out = []
    seen = set()

    for ch in chunks:
        s = " ".join(ch.strip().split())
        if len(s) < 40:
            continue
        sl = s.lower()
        if any(k in sl for k in keywords):
            # Keep it short
            s2 = s[:220] + ("…" if len(s) > 220 else "")
            if s2 not in seen:
                out.append(s2)
                seen.add(s2)
        if len(out) >= max_items:
            break
    return out

def _risk_and_flags(text: str) -> tuple[int, list[str]]:
    t = text.lower()
    flags: list[str] = []
    score = 10  # baseline

    def hit(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)

    # High-impact red flags (tune later)
    hit(("share" in t or "sharing" in t) and ("third party" in t or "third-party" in t), 12,
        "Shares data with third parties (check scope & opt-out).")
    hit("sell" in t and "personal" in t, 18,
        "Mentions selling personal information (verify opt-out/limits).")
    hit("advertising" in t or "ads" in t or "marketing" in t, 8,
        "Data used for advertising/marketing (look for opt-out controls).")
    hit("cookies" in t or "tracking" in t or "analytics" in t, 6,
        "Tracking/cookies/analytics mentioned (check consent controls).")
    hit("retain" in t or "retention" in t or "keep your information" in t, 8,
        "Retention is mentioned (check how long and why).")
    hit("arbitration" in t or "waive" in t and "class action" in t, 14,
        "Arbitration/class-action waiver may limit legal options.")
    hit("we may change" in t and "without notice" in t, 10,
        "Policy may change without notice (weak user protections).")
    hit("location" in t or "precise location" in t or "gps" in t, 10,
        "Collects location data (verify necessity and opt-out).")
    hit("biometric" in t or "face" in t and "recognition" in t, 16,
        "Biometric/face recognition mentioned (high sensitivity).")
    hit("international" in t and ("transfer" in t or "outside" in t), 8,
        "International data transfers mentioned (check safeguards).")

    # Clamp to 0–100
    score = max(0, min(100, score))
    return score, flags[:12]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

def _build_summary(url: str, text: str) -> str:
    # Pick a few sentences that usually matter most
    bullets = []
    bullets += _find_sentences(text, ["collect", "information we collect", "personal information"], max_items=2)
    bullets += _find_sentences(text, ["share", "third party", "service provider"], max_items=2)
    bullets += _find_sentences(text, ["retain", "retention", "keep your"], max_items=1)
    bullets += _find_sentences(text, ["rights", "access", "delete", "opt out", "opt-out"], max_items=2)

    domain = _domain_from_url(url)
    if not bullets:
        return f"Policy detected for {domain}. Key details were not easily extractable from the first 20k characters."
    # Turn into a compact paragraph
    return " ".join(bullets)

# ---------- Route ----------

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")
    # Keep analysis bounded and consistent
    text = text[:20000]

    domain = _domain_from_url(url)
    jurisdiction = _detect_jurisdiction(text)
    score, red_flags = _risk_and_flags(text)
    level = _risk_level(score)
    summary = _build_summary(url, text)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-v1",
    }
