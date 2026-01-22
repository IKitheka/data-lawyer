from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

# -----------------------------
# Helpers: URL / jurisdiction
# -----------------------------
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
    if "kenya" in t or "data protection act, 2019" in t or "office of the data protection commissioner" in t:
        return "KE"
    if "south africa" in t or "popia" in t:
        return "ZA"
    if "brazil" in t or "lgpd" in t:
        return "BR"
    if "united states" in t or "u.s." in t or "usa" in t:
        return "US"
    return "UNKNOWN"

# -----------------------------
# Smarter sentence extraction
# -----------------------------
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def _normalize_ws(s: str) -> str:
    return " ".join(s.strip().split())

def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text)
    out: list[str] = []
    for p in parts:
        s = _normalize_ws(p)
        # keep "policy-like" lines; discard short noise
        if len(s) < 45:
            continue
        # discard menu/footer-like junk
        if s.lower().startswith(("cookie settings", "accept cookies", "all rights reserved")):
            continue
        out.append(s)
    return out

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

def _dedupe_sentences(sents: list[str], sim_threshold: float = 0.72) -> list[str]:
    """
    Remove near-duplicates so evidence doesn't repeat across sections.
    Cheap similarity: Jaccard overlap of word sets.
    """
    kept: list[str] = []
    kept_sets: list[set[str]] = []

    for s in sents:
        ws = set(re.findall(r"[a-z0-9]+", s.lower()))
        duplicate = False
        for ks in kept_sets:
            if _jaccard(ws, ks) >= sim_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(s)
            kept_sets.append(ws)
    return kept

def _find_evidence(text: str, keywords: list[str], max_items: int = 3) -> list[str]:
    """
    Topic-filtered evidence:
    - Sentence must match at least one keyword (substring match)
    - Pull from deduped sentence list
    """
    sents = _dedupe_sentences(_split_sentences(text))
    out: list[str] = []
    for s in sents:
        sl = s.lower()
        if any(k in sl for k in keywords):
            # keep it short for UI readability
            s2 = s[:260] + ("…" if len(s) > 260 else "")
            out.append(s2)
        if len(out) >= max_items:
            break
    return out

# -----------------------------
# Risk model + flags
# -----------------------------
def _risk_and_flags(text: str) -> tuple[int, list[str], list[str]]:
    """
    Returns:
      score (0-100),
      red_flags (max 12),
      concerns (a smaller list used for takeaways)
    """
    t = text.lower()
    flags: list[str] = []
    concerns: list[str] = []
    score = 10

    def hit(cond: bool, points: int, msg: str, concern: str | None = None):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)
            if concern:
                concerns.append(concern)

    hit(("share" in t or "sharing" in t) and ("third party" in t or "third-party" in t), 12,
        "Shares data with third parties (check scope & opt-out).",
        "Sharing with third parties")
    hit("sell" in t and "personal" in t, 18,
        "Mentions selling personal information (verify opt-out/limits).",
        "Selling personal information")
    hit("advertising" in t or "marketing" in t, 8,
        "Data used for advertising/marketing (look for opt-out).",
        "Advertising/marketing use")
    hit("cookies" in t or "tracking" in t or "analytics" in t, 6,
        "Tracking/cookies/analytics mentioned (check consent controls).",
        "Tracking/cookies")
    hit("retain" in t or "retention" in t, 8,
        "Retention mentioned (check duration and purpose).",
        "Retention policy")
    hit("arbitration" in t or ("class action" in t and "waive" in t), 14,
        "Arbitration/class-action waiver may limit legal options.",
        "Legal rights limited")
    hit("without notice" in t and "change" in t, 10,
        "Policy may change without notice (weak user protections).",
        "Changes without notice")
    hit("precise location" in t or "gps" in t or "location data" in t, 10,
        "Collects location data (verify necessity and opt-out).",
        "Location data collection")
    hit("biometric" in t or ("face" in t and "recognition" in t), 16,
        "Biometric/face recognition mentioned (high sensitivity).",
        "Biometric/face recognition")
    hit("international" in t and ("transfer" in t or "outside" in t), 8,
        "International data transfers mentioned (check safeguards).",
        "Cross-border transfers")

    score = max(0, min(100, score))
    return score, flags[:12], concerns[:5]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

# -----------------------------
# Summary builder (v4)
# -----------------------------
SECTIONS = [
    ("Data collected", ["we collect", "information we collect", "personal information", "data we collect", "information you provide"]),
    ("How data is used", ["how we use", "we use", "purpose", "to provide", "improve", "operate", "deliver", "process your"]),
    ("Sharing & third parties", ["share", "sharing", "third party", "third-party", "service provider", "vendors", "partners", "affiliates"]),
    ("Retention", ["retain", "retention", "keep", "storage duration", "as long as", "necessary"]),
    ("Your rights & choices", ["your rights", "access", "delete", "erasure", "opt out", "opt-out", "rectify", "portability", "object"]),
    ("Security", ["security", "safeguard", "protect", "encryption", "access controls", "measures"]),
    ("International transfers", ["international", "transfer", "outside", "cross-border", "other countries"]),
    ("Contact / complaints", ["contact", "email", "privacy@", "dpo", "data protection officer", "complaint", "supervisory authority"]),
]

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _coverage_map(text: str) -> tuple[dict[str, bool], int, list[str]]:
    """
    Returns:
      section_has_evidence map,
      coverage_percent,
      missing_sections
    """
    has: dict[str, bool] = {}
    missing: list[str] = []
    present = 0
    for title, keys in SECTIONS:
        ev = _find_evidence(text, keys, max_items=1)
        ok = bool(ev)
        has[title] = ok
        if ok:
            present += 1
        else:
            missing.append(title)
    pct = int(round((present / len(SECTIONS)) * 100))
    return has, pct, missing

def _action_for(level: str, red_flags: list[str]) -> str:
    l = level.upper()
    if l == "HIGH":
        return "Avoid agreeing unless you must. If you proceed, use a throwaway email, deny optional permissions, and submit a delete/opt-out request."
    if l == "MEDIUM":
        return "Proceed with caution. Review opt-out controls, limit permissions, and check sharing/retention details before accepting."
    # LOW
    if any("third parties" in f.lower() for f in red_flags):
        return "Looks generally low-risk, but still review third-party sharing scope and opt-out options."
    return "Looks generally low-risk. Still confirm what’s collected and how to opt out of marketing/tracking."

def _build_summary(url: str, text: str, level: str, score: int, jurisdiction: str, red_flags: list[str], concerns: list[str]) -> str:
    domain = _domain_from_url(url)
    _, coverage_pct, missing = _coverage_map(text)

    # Evidence per section (topic filtered + deduped)
    evidence_blocks: list[str] = []
    for title, keys in SECTIONS:
        ev = _find_evidence(text, keys, max_items=3)
        evidence_blocks.append(_mk_section(title, ev))

    top_concern = concerns[0] if concerns else (red_flags[0] if red_flags else "No major issues detected in the scanned text.")
    missing_line = ", ".join([f"**{m}**" for m in missing[:4]]) + ("…" if len(missing) > 4 else "")
    action = _action_for(level, red_flags)

    takeaways = []
    takeaways.append(f"- Risk level: **{level}** (score **{score}/100**)")
    takeaways.append(f"- Jurisdiction signals: **{jurisdiction}**")
    takeaways.append(f"- Coverage: **{coverage_pct}%** of key topics detected")
    if missing:
        takeaways.append(f"- Missing/unclear: {missing_line}")
    takeaways.append(f"- Top concern: {top_concern}")
    takeaways.append(f"- Action: {action}")

    summary = []
    summary.append("## Policy Summary")
    summary.append(f"**Site:** {domain}\n")
    summary.append("### Key takeaways")
    summary.append("\n".join(takeaways))
    summary.append("\n### Evidence by section\n")
    summary.append("\n".join(evidence_blocks))

    return "\n".join(summary).strip()

# -----------------------------
# Endpoint
# -----------------------------
@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")[:20000]

    domain = _domain_from_url(url)
    jurisdiction = _detect_jurisdiction(text)

    score, red_flags, concerns = _risk_and_flags(text)
    level = _risk_level(score)

    summary_md = _build_summary(url, text, level, score, jurisdiction, red_flags, concerns)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary_md,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v4",
    }
