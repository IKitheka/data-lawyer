from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

# -----------------------------
# URL / basics
# -----------------------------
def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or url
    except Exception:
        return url

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())

# -----------------------------
# Contact extraction
# -----------------------------
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
_URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

def _extract_contacts(text: str) -> dict:
    t = text or ""
    emails = list(dict.fromkeys([e.lower() for e in _EMAIL_RE.findall(t)]))[:5]

    phones_raw = _PHONE_RE.findall(t)
    phones = []
    for p in phones_raw:
        p2 = _normalize_ws(p)
        if len(re.sub(r"\D", "", p2)) >= 9:
            phones.append(p2)
    phones = list(dict.fromkeys(phones))[:4]

    urls = list(dict.fromkeys(_URL_RE.findall(t)))[:6]

    privacy_paths = []
    for m in re.finditer(r"(?i)\b(/(privacy|contact|support|dpo|data-protection|legal)[^\s]*)", t):
        privacy_paths.append(m.group(1))
    privacy_paths = list(dict.fromkeys(privacy_paths))[:6]

    return {
        "emails": emails,
        "phones": phones,
        "urls": urls,
        "paths": privacy_paths,
    }

# -----------------------------
# Jurisdiction detection
# -----------------------------
def _detect_jurisdiction(text: str) -> str:
    t = (text or "").lower()
    if "gdpr" in t or "european union" in t or "eea" in t:
        return "EU"
    if "california consumer privacy act" in t or "ccpa" in t or "cpra" in t or "do not sell" in t:
        return "US-CA"
    if "united kingdom" in t or "uk gdpr" in t or "ico" in t:
        return "UK"
    if (
        "kenya" in t
        or "data protection act, 2019" in t
        or "data protection act 2019" in t
        or "office of the data protection commissioner" in t
        or "odpc" in t
    ):
        return "KE"
    if "south africa" in t or "popia" in t or "information regulator" in t:
        return "ZA"
    if "brazil" in t or "lgpd" in t:
        return "BR"
    if "united states" in t or "u.s." in t or "usa" in t:
        return "US"
    return "UNKNOWN"

# -----------------------------
# Sentence extraction + dedupe
# -----------------------------
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text or "")
    out: list[str] = []
    for p in parts:
        s = _normalize_ws(p)
        if len(s) < 45:
            continue
        low = s.lower()
        if low.startswith(("cookie settings", "accept cookies", "all rights reserved")):
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

def _find_evidence(text: str, keywords: list[str], max_items: int = 3, used: set[str] | None = None) -> list[str]:
    used = used or set()
    sents = _dedupe_sentences(_split_sentences(text))
    out: list[str] = []
    for s in sents:
        sl = s.lower()
        if any(k in sl for k in keywords):
            s2 = s[:280] + ("…" if len(s) > 280 else "")
            if s2 in used:
                continue
            out.append(s2)
            used.add(s2)
        if len(out) >= max_items:
            break
    return out

def _has_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

# -----------------------------
# Controller / Processor extraction
# -----------------------------
def _extract_controller_hint(text: str, fallback_domain: str) -> str | None:
    sents = _split_sentences(text or "")
    # strongest: "Data Controller: X"
    for s in sents:
        m = re.search(r"(?i)\bdata controller[:\s]\s*(.{3,120})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:120]
    # next: "This policy applies to X"
    for s in sents:
        m = re.search(r"(?i)\bthis policy applies to\s+(.{3,120})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:120]
    return fallback_domain or None

# -----------------------------
# Tiny structured extraction (rule-based)
# -----------------------------
def _extract_structured(text: str, url: str, jurisdiction: str, contacts: dict, controller_hint: str | None) -> dict:
    t = (text or "").lower()

    def yn(keys: list[str]) -> bool:
        return _has_any(t, keys)

    # rights signals (common)
    rights = {
        "access": yn(["right of access", "access your", "access to your", "access request", "data subject access"]),
        "delete": yn(["delete", "erasure", "right to be forgotten", "deletion request"]),
        "correct": yn(["correct", "correction", "rectify", "rectification"]),
        "object": yn(["object", "objection", "right to object"]),
        "portability": yn(["data portability", "portable"]),
        "withdraw_consent": yn(["withdraw consent"]),
        "opt_out_marketing": yn(["opt out", "opt-out", "unsubscribe", "marketing emails"]),
    }

    sharing = {
        "third_parties": yn(["third party", "third-party", "share with", "shared with"]),
        "service_providers": yn(["service provider", "vendor", "processor", "sub-processor"]),
        "sale_of_data": yn(["sell personal", "selling personal", "do not sell"]),
    }

    retention = {
        "mentions_retention": yn(["retain", "retention", "keep for", "as long as necessary"]),
        "mentions_duration": yn(["for x years", "for a period", "until no longer", "as long as necessary"]),
    }

    tracking = {
        "cookies_or_tracking": yn(["cookie", "tracking", "analytics", "pixel"]),
        "ads_marketing": yn(["advertising", "marketing", "targeted ads", "personalized ads"]),
    }

    legal = {
        "arbitration": yn(["arbitration"]),
        "class_action_waiver": yn(["class action", "waive", "waiver"]),
    }

    cross_border = {
        "international_transfers": yn(["international transfer", "cross-border", "outside", "other countries"]),
        "safeguards": yn(["standard contractual clauses", "scc", "adequacy", "safeguards"]),
    }

    complaints = {
        "mentions_supervisory_authority": yn(["supervisory authority", "lodge a complaint"]),
        "mentions_odpc": yn(["odpc", "office of the data protection commissioner"]),
    }

    # Kenya checklist booleans (simple but useful for UI)
    ke = {
        "controller_identified": bool(controller_hint) or yn(["data controller", "controller", "data processor", "processor"]),
        "contact_provided": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")) or yn(["contact", "privacy@", "dpo"]),
        "rights_mentioned": any(rights.values()) or yn(["data subject"]),
        "purpose_mentioned": yn(["purpose", "how we use", "we use", "for the purpose"]),
        "retention_mentioned": retention["mentions_retention"],
        "sharing_disclosed": sharing["third_parties"] or sharing["service_providers"],
        "cross_border_mentioned": cross_border["international_transfers"],
        "complaints_route": complaints["mentions_odpc"] or complaints["mentions_supervisory_authority"] or yn(["complaint"]),
    }

    return {
        "site": _domain_from_url(url),
        "jurisdiction": jurisdiction,
        "controller_hint": controller_hint,
        "contacts": {
            "emails": contacts.get("emails", []),
            "phones": contacts.get("phones", []),
            "paths": contacts.get("paths", []),
        },
        "rights": rights,
        "sharing": sharing,
        "retention": retention,
        "tracking": tracking,
        "legal": legal,
        "cross_border": cross_border,
        "complaints": complaints,
        "ke_checklist": ke if jurisdiction == "KE" else None,
    }

# -----------------------------
# Risk model + flags
# -----------------------------
def _risk_and_flags(text: str) -> tuple[int, list[str], list[str]]:
    t = (text or "").lower()
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
# Evidence sections
# -----------------------------
SECTIONS = [
    ("Data collected", ["we collect", "information we collect", "personal information", "data we collect", "information you provide", "categories of personal data"]),
    ("How data is used", ["how we use", "we use", "purpose", "to provide", "improve", "operate", "deliver", "process your"]),
    ("Sharing & third parties", ["share", "sharing", "third party", "third-party", "service provider", "vendors", "partners", "affiliates"]),
    ("Retention", ["retain", "retention", "keep", "storage duration", "as long as", "necessary"]),
    ("Your rights & choices", ["your rights", "access", "delete", "erasure", "opt out", "opt-out", "rectify", "portability", "object", "withdraw consent"]),
    ("Security", ["security", "safeguard", "protect", "encryption", "access controls", "measures"]),
    ("International transfers", ["international", "transfer", "outside", "cross-border", "other countries"]),
    ("Contact / complaints", ["contact", "email", "privacy@", "dpo", "data protection officer", "complaint", "supervisory authority", "odpc", "office of the data protection commissioner"]),
]

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _coverage_map(text: str) -> tuple[int, list[str]]:
    missing: list[str] = []
    present = 0
    used = set()
    for title, keys in SECTIONS:
        ev = _find_evidence(text, keys, max_items=1, used=used)
        if ev:
            present += 1
        else:
            missing.append(title)
    pct = int(round((present / len(SECTIONS)) * 100))
    return pct, missing

# -----------------------------
# Kenya-aware risk adjustment
# -----------------------------
def _kenya_risk_adjust(score: int, structured: dict) -> tuple[int, list[str]]:
    extra: list[str] = []
    add = 0

    ke = structured.get("ke_checklist") or {}
    if not ke:
        return score, extra

    if not ke.get("controller_identified"):
        add += 10
        extra.append("Kenya: Missing/unclear data controller identity.")
    if not ke.get("contact_provided"):
        add += 8
        extra.append("Kenya: Missing/unclear privacy contact method.")
    if not ke.get("rights_mentioned"):
        add += 8
        extra.append("Kenya: Missing/unclear data subject rights section.")
    if not ke.get("purpose_mentioned"):
        add += 6
        extra.append("Kenya: Missing/unclear purpose of processing.")
    if not ke.get("complaints_route"):
        add += 5
        extra.append("Kenya: No clear complaint route (ODPC or equivalent).")

    new_score = max(0, min(100, score + add))
    return new_score, extra[:5]

# -----------------------------
# Takeaways helper
# -----------------------------
def _action_for(level: str, red_flags: list[str]) -> str:
    l = level.upper()
    if l == "HIGH":
        return "Avoid agreeing unless necessary. If you proceed, minimize permissions and submit deletion/opt-out requests."
    if l == "MEDIUM":
        return "Proceed with caution. Review opt-out controls, sharing scope, and retention details before accepting."
    if any("third parties" in f.lower() for f in red_flags):
        return "Low risk overall, but verify third-party sharing scope and available opt-out controls."
    return "Low risk overall. Still confirm what’s collected and how to opt out of marketing/tracking."

# -----------------------------
# Summary builder
# -----------------------------
def _build_summary(url: str, text: str, level: str, score: int, structured: dict, red_flags: list[str], concerns: list[str]) -> str:
    domain = _domain_from_url(url)
    coverage_pct, missing_sections = _coverage_map(text)

    controller_hint = structured.get("controller_hint")
    contacts = structured.get("contacts") or {}
    ke = structured.get("ke_checklist")

    top_concern = concerns[0] if concerns else (red_flags[0] if red_flags else "No major issues detected in the scanned text.")
    missing_line = ", ".join([f"**{m}**" for m in missing_sections[:4]]) + ("…" if len(missing_sections) > 4 else "")
    action = _action_for(level, red_flags)

    contact_hint_line = None
    parts = []
    if contacts.get("emails"):
        parts.append("email: " + ", ".join(contacts["emails"]))
    if contacts.get("phones"):
        parts.append("phone: " + ", ".join(contacts["phones"]))
    if contacts.get("paths"):
        parts.append("link: " + ", ".join(contacts["paths"]))
    if parts:
        contact_hint_line = " | ".join(parts)

    takeaways = []
    takeaways.append(f"- Risk level: **{level}** (score **{score}/100**)")
    takeaways.append(f"- Jurisdiction signals: **{structured.get('jurisdiction')}**")
    takeaways.append(f"- Coverage: **{coverage_pct}%** of key topics detected")
    if missing_sections:
        takeaways.append(f"- Missing/unclear: {missing_line}")
    takeaways.append(f"- Top concern: {top_concern}")
    if controller_hint:
        takeaways.append(f"- Likely controller/org: **{controller_hint}**")
    if contact_hint_line:
        takeaways.append(f"- Contact hints: {contact_hint_line}")
    takeaways.append(f"- Action: {action}")

    # Evidence (global de-dupe)
    used = set()
    evidence_blocks: list[str] = []
    for title, keys in SECTIONS:
        ev = _find_evidence(text, keys, max_items=3, used=used)
        evidence_blocks.append(_mk_section(title, ev))

    summary = []
    summary.append("## Policy Summary")
    summary.append(f"**Site:** {domain}\n")
    summary.append("### Key takeaways")
    summary.append("\n".join(takeaways))

    if ke:
        summary.append("\n### Kenya checklist")
        summary.append("- ✅/❌ based on what was detected in the first 20k characters.")
        for k, v in ke.items():
            label = k.replace("_", " ").capitalize()
            summary.append(f"- {'✅' if v else '❌'} {label}")

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

    contacts = _extract_contacts(text)
    controller_hint = _extract_controller_hint(text, domain)

    # structured extraction first (so we can use it in scoring + UI)
    structured = _extract_structured(text, url, jurisdiction, contacts, controller_hint)

    score, red_flags, concerns = _risk_and_flags(text)

    # Kenya: adjust risk upward when essentials are missing
    if jurisdiction == "KE":
        score, extra_ke_flags = _kenya_risk_adjust(score, structured)
        for f in extra_ke_flags:
            if f not in red_flags:
                red_flags.append(f)
        red_flags = red_flags[:12]

    level = _risk_level(score)
    summary_md = _build_summary(url, text, level, score, structured, red_flags, concerns)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary_md,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v8-structured",
        "structured": structured,  # ✅ new: rule-based JSON extraction
    }
