from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or url
    except Exception:
        return url

def _detect_jurisdiction(text: str) -> str:
    t = text.lower()
    if "gdpr" in t or "european union" in t or "eea" in t:
        return "EU"
    if "california consumer privacy act" in t or "ccpa" in t or "cpra" in t:
        return "US-CA"
    if "united kingdom" in t or "uk gdpr" in t:
        return "UK"
    if "kenya" in t or "data protection act, 2019" in t:
        return "KE"
    if "south africa" in t or "popia" in t:
        return "ZA"
    if "united states" in t or "u.s." in t or "usa" in t:
        return "US"
    return "UNKNOWN"

def _split_sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    out = []
    for ch in chunks:
        s = " ".join(ch.strip().split())
        if len(s) >= 40:
            out.append(s)
    return out

def _pick_examples(text: str, keywords: list[str], max_items: int = 3) -> list[str]:
    sents = _split_sentences(text)
    out, seen = [], set()
    for s in sents:
        sl = s.lower()
        if any(k in sl for k in keywords):
            s2 = s[:240] + ("…" if len(s) > 240 else "")
            if s2 not in seen:
                out.append(s2)
                seen.add(s2)
        if len(out) >= max_items:
            break
    return out

def _risk_and_flags(text: str) -> tuple[int, list[str]]:
    t = text.lower()
    flags = []
    score = 10

    def hit(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)

    hit(("share" in t or "sharing" in t) and ("third party" in t or "third-party" in t), 12,
        "Shares data with third parties (check scope & opt-out).")
    hit("sell" in t and "personal" in t, 18,
        "Mentions selling personal information (verify opt-out/limits).")
    hit("advertising" in t or "marketing" in t, 8,
        "Data used for advertising/marketing (look for opt-out).")
    hit("cookies" in t or "tracking" in t or "analytics" in t, 6,
        "Tracking/cookies/analytics mentioned (check consent controls).")
    hit("retain" in t or "retention" in t, 8,
        "Retention mentioned (check duration and purpose).")
    hit("arbitration" in t or ("class action" in t and "waive" in t), 14,
        "Arbitration/class-action waiver may limit legal options.")
    hit("without notice" in t and "change" in t, 10,
        "Policy may change without notice (weak user protections).")
    hit("precise location" in t or "gps" in t or "location data" in t, 10,
        "Collects location data (verify necessity and opt-out).")
    hit("biometric" in t or ("face" in t and "recognition" in t), 16,
        "Biometric/face recognition mentioned (high sensitivity).")
    hit("international" in t and ("transfer" in t or "outside" in t), 8,
        "International data transfers mentioned (check safeguards).")

    score = max(0, min(100, score))
    return score, flags[:12]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _build_sectioned_summary(url: str, text: str) -> str:
    # Extract evidence-like lines (small snippets) per section
    collected = _pick_examples(text, ["information we collect", "we collect", "personal information", "data we collect"], 3)
    used = _pick_examples(text, ["how we use", "we use", "purpose", "to provide", "improve"], 3)
    sharing = _pick_examples(text, ["share", "sharing", "third party", "service provider"], 3)
    retention = _pick_examples(text, ["retain", "retention", "keep your", "storage duration"], 2)
    rights = _pick_examples(text, ["your rights", "access", "delete", "erasure", "opt out", "opt-out", "rectify"], 3)
    security = _pick_examples(text, ["security", "safeguard", "protect", "encryption"], 2)
    transfers = _pick_examples(text, ["international", "transfer", "outside", "cross-border"], 2)
    contact = _pick_examples(text, ["contact", "email", "dpo", "data protection officer"], 2)

    domain = _domain_from_url(url)

    summary = []
    summary.append(f"## Policy Summary (sectioned)\n**Site:** {domain}\n")
    summary.append(_mk_section("Data collected", collected))
    summary.append(_mk_section("How data is used", used))
    summary.append(_mk_section("Sharing & third parties", sharing))
    summary.append(_mk_section("Retention", retention))
    summary.append(_mk_section("Your rights & choices", rights))
    summary.append(_mk_section("Security", security))
    summary.append(_mk_section("International transfers", transfers))
    summary.append(_mk_section("Contact / complaints", contact))

    return "\n".join(summary).strip()

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")[:20000]

    domain = _domain_from_url(url)
    jurisdiction = _detect_jurisdiction(text)
    score, red_flags = _risk_and_flags(text)
    level = _risk_level(score)

    sectioned_summary = _build_sectioned_summary(url, text)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": sectioned_summary,            # <— now sectioned markdown
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v1",
    }
