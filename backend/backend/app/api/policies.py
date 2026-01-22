from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

MAX_CHARS = 20000


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or url
    except Exception:
        return url


def _detect_jurisdiction(text: str) -> str:
    t = text.lower()
    # Very lightweight signals; you can expand later.
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


def _normalize_space(s: str) -> str:
    return " ".join((s or "").strip().split())


def _split_sentences(text: str) -> list[str]:
    """
    Split into sentence-ish chunks.
    - Break on punctuation boundaries or newlines.
    - Keep only reasonably informative lines.
    """
    chunks = re.split(r"(?<=[.!?])\s+|\n{2,}|\r\n{2,}", text)
    out: list[str] = []
    for ch in chunks:
        s = _normalize_space(ch)
        # Keep lines that likely contain meaning. (Avoid nav/footer junk.)
        if 45 <= len(s) <= 420:
            out.append(s)
    return out


def _risk_and_flags(text: str) -> tuple[int, list[str]]:
    t = text.lower()
    flags: list[str] = []
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
        return f"### {title}\n- Not clearly stated in the first {MAX_CHARS//1000}k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"


def _score_sentence(sentence: str, keywords: list[str]) -> int:
    """
    Score a sentence by:
    - keyword hits
    - strong modal verbs that often indicate policy commitments
    - avoiding low-value boilerplate
    """
    s = sentence.lower()
    score = 0

    # keyword hits
    for k in keywords:
        if k in s:
            score += 3

    # "policy voice" signals
    if "we collect" in s or "we may collect" in s:
        score += 4
    if "we use" in s or "we may use" in s:
        score += 3
    if "we share" in s or "we may share" in s:
        score += 4
    if "you may" in s or "you can" in s:
        score += 2
    if "opt out" in s or "opt-out" in s:
        score += 3

    # lightly downscore likely boilerplate/navigation junk
    junk = ["copyright", "all rights reserved", "cookie preferences", "skip to content", "accept cookies"]
    if any(j in s for j in junk):
        score -= 4

    # length heuristic: too short or too long reduces quality
    n = len(sentence)
    if n < 70:
        score -= 1
    if n > 280:
        score -= 1

    return score


def _top_evidence(text: str, keywords: list[str], max_items: int) -> list[str]:
    """
    Pick the strongest sentences for a section.
    """
    sents = _split_sentences(text)
    scored: list[tuple[int, str]] = []
    seen = set()

    for s in sents:
        sc = _score_sentence(s, keywords)
        if sc <= 0:
            continue
        # require at least one keyword hit OR a strong "we ..." signal
        sl = s.lower()
        if not any(k in sl for k in keywords) and not (
            "we collect" in sl or "we use" in sl or "we share" in sl or "opt out" in sl or "opt-out" in sl
        ):
            continue

        s2 = s[:240] + ("…" if len(s) > 240 else "")
        if s2 in seen:
            continue
        seen.add(s2)
        scored.append((sc, s2))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_items]]


def _build_takeaways(domain: str, jurisdiction: str, score: int, level: str, red_flags: list[str], sections_found: dict) -> list[str]:
    """
    Build 3–6 bullets that are human-scannable.
    """
    takeaways: list[str] = []

    takeaways.append(f"Risk level: **{level}** (score **{score}/100**)")

    if jurisdiction and jurisdiction != "UNKNOWN":
        takeaways.append(f"Jurisdiction signals: **{jurisdiction}**")
    else:
        takeaways.append("Jurisdiction signals: **UNKNOWN** (no clear legal references found)")

    # If we found no evidence for some key sections, highlight that.
    missing = [k for k, v in sections_found.items() if not v]
    if missing:
        # Keep it short
        takeaways.append("Missing/unclear: " + ", ".join([f"**{m}**" for m in missing[:4]]) + ("…" if len(missing) > 4 else ""))

    if red_flags:
        takeaways.append("Top concern: " + red_flags[0])

    # Add one more "action" style tip
    if "Your rights & choices" in missing:
        takeaways.append("Action: Look for an **opt-out / delete request** link or contact email before accepting.")
    else:
        takeaways.append("Action: Review **opt-out/delete** steps and whether they apply to your country.")

    return takeaways[:6]


def _build_sectioned_summary(url: str, text: str, jurisdiction: str, score: int, level: str, red_flags: list[str]) -> str:
    domain = _domain_from_url(url)

    # Expanded keywords per section (cover common phrasing variants)
    sections = [
        ("Data collected", [
            "information we collect", "we collect", "data we collect", "personal information",
            "information you provide", "device information", "log data", "usage data"
        ], 3),
        ("How data is used", [
            "how we use", "we use", "purpose", "to provide", "to improve", "to operate", "to maintain",
            "to personalize", "to communicate", "to comply", "legal basis"
        ], 3),
        ("Sharing & third parties", [
            "we share", "share", "sharing", "third party", "third-party", "service provider",
            "affiliates", "partners", "vendors", "processors"
        ], 3),
        ("Retention", [
            "retain", "retention", "keep", "storage duration", "how long", "we store"
        ], 2),
        ("Your rights & choices", [
            "your rights", "access", "delete", "erasure", "rectify", "opt out", "opt-out",
            "object", "appeal", "data subject", "unsubscribe"
        ], 3),
        ("Security", [
            "security", "safeguard", "protect", "encryption", "secure", "technical and organizational"
        ], 2),
        ("International transfers", [
            "international", "transfer", "outside", "cross-border", "standard contractual", "scc"
        ], 2),
        ("Contact / complaints", [
            "contact", "email", "dpo", "data protection officer", "complaint", "supervisory authority"
        ], 2),
    ]

    found: dict[str, list[str]] = {}
    for title, kws, n in sections:
        found[title] = _top_evidence(text, kws, n)

    takeaways = _build_takeaways(domain, jurisdiction, score, level, red_flags, found)

    out: list[str] = []
    out.append("## Policy Summary")
    out.append(f"**Site:** {domain}")
    out.append("")
    out.append("### Key takeaways")
    out.extend([f"- {t}" for t in takeaways])
    out.append("")
    out.append("### Evidence by section")
    out.append("")
    for title, _kws, _n in sections:
        out.append(_mk_section(title, found.get(title, [])))

    return "\n".join(out).strip()


@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")[:MAX_CHARS]

    domain = _domain_from_url(url)
    jurisdiction = _detect_jurisdiction(text)
    score, red_flags = _risk_and_flags(text)
    level = _risk_level(score)

    summary_md = _build_sectioned_summary(url, text, jurisdiction, score, level, red_flags)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary_md,  # markdown
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v2",
    }
