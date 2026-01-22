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
    chunks = re.split(r"(?<=[.!?])\s+|\n{1,}|\r\n{1,}", text)
    out: list[str] = []
    for ch in chunks:
        s = _normalize_space(ch)
        if 20 <= len(s) <= 520:
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


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _section_config():
    # Keywords = general hints
    # strong = section-specific “must belong here” signals
    # avoid = terms that suggest it belongs elsewhere
    return [
        {
            "title": "Data collected",
            "keywords": ["collect", "personal information", "information you provide", "device information", "usage data", "identifiers"],
            "strong": ["we collect", "we may collect", "information we collect", "data we collect"],
            "avoid": ["opt out", "unsubscribe", "contact us"],
            "max_items": 3,
        },
        {
            "title": "How data is used",
            "keywords": ["use", "purpose", "to provide", "to improve", "to operate", "to maintain", "to personalize", "to comply"],
            "strong": ["we use", "we may use", "how we use"],
            "avoid": ["contact us"],
            "max_items": 3,
        },
        {
            "title": "Sharing & third parties",
            "keywords": ["share", "third party", "third-party", "service provider", "vendors", "partners", "affiliates", "processors"],
            "strong": ["we share", "we may share"],
            "avoid": ["opt out", "unsubscribe"],
            "max_items": 3,
        },
        {
            "title": "Retention",
            "keywords": ["retain", "retention", "how long", "as long as", "keep"],
            "strong": ["we retain", "we may retain", "we keep", "as long as necessary"],
            "avoid": ["contact us", "opt out", "unsubscribe"],
            "max_items": 2,
        },
        {
            "title": "Your rights & choices",
            "keywords": ["your rights", "opt out", "opt-out", "unsubscribe", "delete", "access", "rectify", "erasure", "object"],
            "strong": ["you may opt out", "you can opt out", "you may delete", "you can delete", "unsubscribe"],
            "avoid": ["encryption", "safeguard"],
            "max_items": 3,
        },
        {
            "title": "Security",
            "keywords": ["security", "safeguard", "protect", "encryption", "secure"],
            "strong": ["we protect", "we safeguard", "encryption"],
            "avoid": ["opt out", "unsubscribe", "contact us"],
            "max_items": 2,
        },
        {
            "title": "International transfers",
            "keywords": ["international", "transfer", "outside", "cross-border", "scc"],
            "strong": ["international transfer", "cross-border"],
            "avoid": ["opt out", "unsubscribe"],
            "max_items": 2,
        },
        {
            "title": "Contact / complaints",
            "keywords": ["contact", "reach us", "complaint", "dpo", "data protection officer", "email"],
            "strong": ["contact us", "reach us", "dpo"],
            "avoid": ["we share", "we retain"],
            "max_items": 2,
        },
    ]


def _score_for_section(sentence: str, cfg: dict) -> int:
    s = sentence.lower()
    score = 0

    # keyword hits
    for k in cfg["keywords"]:
        if k in s:
            score += 3

    # strong section signals
    for p in cfg["strong"]:
        if p in s:
            score += 7

    # contact section boost if email exists
    if cfg["title"] == "Contact / complaints" and EMAIL_RE.search(sentence):
        score += 10

    # penalties for “belongs elsewhere”
    for a in cfg.get("avoid", []):
        if a in s:
            score -= 4

    # light length shaping
    n = len(sentence)
    if n < 35:
        score -= 1
    if n > 320:
        score -= 1

    return score


def _assign_sentences_to_sections(text: str) -> dict[str, list[str]]:
    cfgs = _section_config()
    buckets: dict[str, list[tuple[int, str]]] = {c["title"]: [] for c in cfgs}

    sents = _split_sentences(text)
    seen_global = set()

    for sent in sents:
        # score against all sections, choose best one
        best_title = None
        best_score = -999

        for c in cfgs:
            sc = _score_for_section(sent, c)

            # must have at least some real signal for that section
            if sc < 4:
                continue

            if sc > best_score:
                best_score = sc
                best_title = c["title"]

        if not best_title:
            continue

        # global de-dupe so one sentence only appears once in whole summary
        s2 = sent[:240] + ("…" if len(sent) > 240 else "")
        if s2 in seen_global:
            continue
        seen_global.add(s2)

        buckets[best_title].append((best_score, s2))

    # sort & trim
    for c in cfgs:
        title = c["title"]
        buckets[title].sort(key=lambda x: x[0], reverse=True)
        buckets[title] = [s for _, s in buckets[title][: c["max_items"]]]

    return buckets


def _build_takeaways(domain: str, jurisdiction: str, score: int, level: str, red_flags: list[str], buckets: dict[str, list[str]]) -> list[str]:
    takeaways: list[str] = []
    takeaways.append(f"Risk level: **{level}** (score **{score}/100**)")

    if jurisdiction and jurisdiction != "UNKNOWN":
        takeaways.append(f"Jurisdiction signals: **{jurisdiction}**")
    else:
        takeaways.append("Jurisdiction signals: **UNKNOWN** (no clear legal references found)")

    missing = [k for k, v in buckets.items() if not v]
    if missing:
        takeaways.append("Missing/unclear: " + ", ".join([f"**{m}**" for m in missing[:4]]) + ("…" if len(missing) > 4 else ""))

    if red_flags:
        takeaways.append("Top concern: " + red_flags[0])

    if buckets.get("Your rights & choices"):
        takeaways.append("Action: Review **opt-out/delete** steps and whether they apply to your country.")
    else:
        takeaways.append("Action: Look for an **opt-out / delete request** link or contact email before accepting.")

    return takeaways[:6]


def _build_summary(url: str, text: str, jurisdiction: str, score: int, level: str, red_flags: list[str]) -> str:
    domain = _domain_from_url(url)
    buckets = _assign_sentences_to_sections(text)

    takeaways = _build_takeaways(domain, jurisdiction, score, level, red_flags, buckets)

    out: list[str] = []
    out.append("## Policy Summary")
    out.append(f"**Site:** {domain}")
    out.append("")
    out.append("### Key takeaways")
    out.extend([f"- {t}" for t in takeaways])
    out.append("")
    out.append("### Evidence by section")
    out.append("")

    for c in _section_config():
        title = c["title"]
        out.append(_mk_section(title, buckets.get(title, [])))

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

    summary_md = _build_summary(url, text, jurisdiction, score, level, red_flags)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary_md,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v4",
    }
