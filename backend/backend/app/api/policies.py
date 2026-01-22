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

    patterns = [
        r"(?i)\bdata controller[:\s]\s*(.{3,140})",
        r"(?i)\bcontroller[:\s]\s*(.{3,140})",
        r"(?i)\bwe are (the )?data controller\b",
        r"(?i)\bwe are (the )?controller\b",
    ]

    for s in sents:
        for pat in patterns[:2]:
            m = re.search(pat, s)
            if m:
                val = _normalize_ws(m.group(1))
                val = re.split(r"[.;\n]", val)[0].strip()
                return val[:140]

    # If it literally says "we are the controller" but doesn't give a name, fallback to domain
    if any(re.search(p, (text or ""), re.IGNORECASE) for p in patterns[2:]):
        return fallback_domain or None

    # next: "This policy applies to X"
    for s in sents:
        m = re.search(r"(?i)\bthis policy applies to\s+(.{3,140})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:140]

    return fallback_domain or None

# -----------------------------
# Risk + flags
# -----------------------------
def _risk_and_flags(text: str, jurisdiction: str, structured: dict) -> tuple[int, list[str]]:
    t = (text or "").lower()
    flags: list[str] = []
    score = 10

    def hit(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)

    hit(structured["sharing"]["sale_of_data"], 18, "Mentions selling personal information (verify opt-out/limits).")
    hit(structured["tracking"]["ads_marketing"], 8, "Data used for advertising/marketing (look for opt-out).")
    hit(structured["tracking"]["cookies_or_tracking"], 6, "Tracking/cookies/analytics mentioned (check consent controls).")
    hit(structured["retention"]["mentions_retention"], 8, "Retention mentioned (check duration and purpose).")
    hit("arbitration" in t, 14, "Arbitration may limit legal options.")
    hit(("class action" in t and ("waive" in t or "waiver" in t)), 14, "Class-action waiver may limit legal options.")
    hit(("without notice" in t and "change" in t), 10, "Policy may change without notice (weak user protections).")
    hit(("precise location" in t or "gps" in t or "location data" in t), 10, "Collects location data (verify necessity and opt-out).")
    hit(("biometric" in t or ("face" in t and "recognition" in t)), 16, "Biometric/face recognition mentioned (high sensitivity).")
    hit(structured["cross_border"]["international_transfers"], 8, "International data transfers mentioned (check safeguards).")

    # Kenya-specific nudge: purpose missing
    if jurisdiction == "KE" and not structured["ke_checklist"]["purpose_mentioned"]:
        flags.append("Kenya: Missing/unclear purpose of processing.")

    score = max(0, min(100, score))
    return score, flags[:12]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

# -----------------------------
# Tiny structured extraction (rule-based)
# -----------------------------
def _extract_structured(text: str, url: str, jurisdiction: str, contacts: dict, controller_hint: str | None) -> dict:
    tlow = (text or "").lower()

    def yn(keys: list[str]) -> bool:
        return _has_any(tlow, keys)

    rights = {
        "access": yn([
            "right of access", "request access", "access your", "access to your", "access request",
            "subject access", "data subject access", "obtain a copy", "copy of your"
        ]),
        "delete": yn(["delete", "deletion", "erase", "erasure", "right to be forgotten", "remove your data"]),
        "correct": yn(["correct", "correction", "rectify", "rectification"]),
        "object": yn(["right to object", "object to", "objection"]),
        "portability": yn(["data portability", "portable format", "machine-readable"]),
        "withdraw_consent": yn(["withdraw consent", "withdraw your consent"]),
        "opt_out_marketing": yn(["opt out", "opt-out", "unsubscribe", "marketing emails"]),
    }

    sharing = {
        "third_parties": yn(["third party", "third-party", "share with", "shared with"]),
        "service_providers": yn(["service provider", "vendor", "processor", "sub-processor"]),
        "sale_of_data": yn(["sell personal", "selling personal", "do not sell"]),
    }

    retention = {
        "mentions_retention": yn(["retain data", "retain", "retention", "keep for", "keep your data", "as long as necessary"]),
        "mentions_duration": yn(["as long as necessary", "for a period", "until no longer", "x years", "months", "days"]),
    }

    tracking = {
        "cookies_or_tracking": yn(["cookie", "tracking", "analytics", "pixel"]),
        "ads_marketing": yn(["advertising", "marketing", "targeted ads", "personalized ads", "promotional"]),
    }

    legal = {
        "arbitration": yn(["arbitration"]),
        "class_action_waiver": yn(["class action", "waive", "waiver"]),
    }

    cross_border = {
        "international_transfers": yn(["international transfer", "cross-border", "transfer outside", "outside your country", "other countries"]),
        "safeguards": yn(["standard contractual clauses", "scc", "adequacy", "safeguards"]),
    }

    complaints = {
        "mentions_supervisory_authority": yn(["supervisory authority", "lodge a complaint", "complaint to"]),
        "mentions_odpc": yn(["odpc", "office of the data protection commissioner"]),
    }

    # Purpose detection (tighter than "we use")
    purpose_mentioned = yn([
        "purpose", "purposes", "used for", "we use your", "how we use",
        "to provide", "to improve", "to operate", "to comply", "legal obligation"
    ])

    ke = {
        "controller_identified": bool(controller_hint) or yn(["data controller", "controller", "data processor", "processor"]),
        "contact_provided": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")) or yn(["contact", "dpo", "privacy@"]),
        "rights_mentioned": any(rights.values()) or yn(["data subject"]),
        "purpose_mentioned": purpose_mentioned,
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
        "ke_checklist": ke,
    }

# -----------------------------
# Summary builder (uses structured + clean evidence)
# -----------------------------
def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _coverage(structured: dict) -> int:
    checks = [
        bool(structured["controller_hint"]),
        bool(structured["contacts"]["emails"] or structured["contacts"]["phones"] or structured["contacts"]["paths"]),
        any(structured["rights"].values()),
        structured["sharing"]["third_parties"] or structured["sharing"]["service_providers"],
        structured["retention"]["mentions_retention"],
        structured["tracking"]["cookies_or_tracking"] or structured["tracking"]["ads_marketing"],
        structured["cross_border"]["international_transfers"],
        structured["complaints"]["mentions_odpc"] or structured["complaints"]["mentions_supervisory_authority"],
    ]
    pct = int(round(100 * (sum(1 for x in checks if x) / len(checks))))
    return pct

def _build_summary(url: str, text: str, level: str, score: int, jurisdiction: str, red_flags: list[str], structured: dict) -> str:
    used: set[str] = set()

    # evidence keywords (section specific, improved)
    collected_kw = ["information we collect", "data we collect", "we collect", "personal information", "collect information"]
    used_kw = ["how we use", "we use your", "used for", "to provide", "to improve", "purpose", "legal obligation"]
    sharing_kw = ["share", "sharing", "third party", "third-party", "service provider", "vendor", "processor"]
    retention_kw = ["retain data", "retain", "retention", "keep your data", "as long as necessary", "storage duration"]
    rights_kw = ["your rights", "right to", "access", "delete", "deletion", "erase", "erasure", "opt out", "unsubscribe", "withdraw consent"]
    security_kw = ["security", "safeguard", "protect", "encryption", "access controls", "technical and organizational"]
    transfers_kw = ["international", "cross-border", "transfer outside", "other countries", "scc", "standard contractual clauses"]
    contact_kw = ["contact", "email", "privacy@", "dpo", "data protection officer", "odpc", "complaint", "supervisory authority"]

    # pull evidence (deduped globally via `used`)
    collected = _find_evidence(text, collected_kw, 3, used)
    used_e = _find_evidence(text, used_kw, 3, used)
    sharing = _find_evidence(text, sharing_kw, 3, used)
    retention = _find_evidence(text, retention_kw, 2, used)
    rights = _find_evidence(text, rights_kw, 3, used)
    security = _find_evidence(text, security_kw, 2, used)
    transfers = _find_evidence(text, transfers_kw, 2, used)
    contact = _find_evidence(text, contact_kw, 2, used)

    domain = _domain_from_url(url)
    coverage = _coverage(structured)

    # key takeaways
    top_concern = red_flags[0] if red_flags else None
    action = "Review opt-out/delete steps and whether they apply to your country."
    if level == "HIGH":
        action = "High risk: avoid accepting until you find opt-out/delete steps and sharing limits."
    elif level == "MEDIUM":
        action = "Medium risk: confirm sharing, retention duration, and how to delete/opt-out."

    missing = []
    if not collected: missing.append("Data collected")
    if not used_e: missing.append("How data is used")
    if not sharing: missing.append("Sharing & third parties")
    if not retention: missing.append("Retention")
    if not rights: missing.append("Your rights & choices")

    missing_str = ""
    if missing:
        missing_str = f"- Missing/unclear: **{', '.join(missing[:5])}**…\n"

    lines = []
    lines.append("## Policy Summary")
    lines.append(f"**Site:** {domain}\n")
    lines.append("### Key takeaways")
    lines.append(f"- Risk level: **{level}** (score **{score}/100**)")
    lines.append(f"- Jurisdiction signals: **{jurisdiction}**")
    lines.append(f"- Coverage: **{coverage}%** of key topics detected")
    if missing_str:
        lines.append(missing_str.rstrip())
    if top_concern:
        lines.append(f"- Top concern: {top_concern}")
    if structured.get("controller_hint"):
        lines.append(f"- Likely controller/org: **{structured['controller_hint']}**")
    if structured["contacts"]["emails"]:
        lines.append(f"- Contact hints: email: {', '.join(structured['contacts']['emails'][:2])}")
    lines.append(f"- Action: {action}\n")

    # Kenya checklist
    if jurisdiction == "KE":
        ke = structured["ke_checklist"]
        lines.append("### Kenya checklist")
        lines.append("- ✅/❌ based on what was detected in the first 20k characters.")
        lines.append(f"- {'✅' if ke['controller_identified'] else '❌'} Controller identified")
        lines.append(f"- {'✅' if ke['contact_provided'] else '❌'} Contact provided")
        lines.append(f"- {'✅' if ke['rights_mentioned'] else '❌'} Rights mentioned")
        lines.append(f"- {'✅' if ke['purpose_mentioned'] else '❌'} Purpose mentioned")
        lines.append(f"- {'✅' if ke['retention_mentioned'] else '❌'} Retention mentioned")
        lines.append(f"- {'✅' if ke['sharing_disclosed'] else '❌'} Sharing disclosed")
        lines.append(f"- {'✅' if ke['cross_border_mentioned'] else '❌'} Cross border mentioned")
        lines.append(f"- {'✅' if ke['complaints_route'] else '❌'} Complaints route\n")

    lines.append("### Evidence by section\n")
    lines.append(_mk_section("Data collected", collected))
    lines.append(_mk_section("How data is used", used_e))
    lines.append(_mk_section("Sharing & third parties", sharing))
    lines.append(_mk_section("Retention", retention))
    lines.append(_mk_section("Your rights & choices", rights))
    lines.append(_mk_section("Security", security))
    lines.append(_mk_section("International transfers", transfers))
    lines.append(_mk_section("Contact / complaints", contact))

    return "\n".join(lines).strip()

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")[:20000]

    domain = _domain_from_url(url)
    jurisdiction = _detect_jurisdiction(text)
    contacts = _extract_contacts(text)
    controller_hint = _extract_controller_hint(text, domain)

    structured = _extract_structured(
        text=text,
        url=url,
        jurisdiction=jurisdiction,
        contacts=contacts,
        controller_hint=controller_hint,
    )

    score, red_flags = _risk_and_flags(text, jurisdiction, structured)
    level = _risk_level(score)

    summary = _build_summary(
        url=url,
        text=text,
        level=level,
        score=score,
        jurisdiction=jurisdiction,
        red_flags=red_flags,
        structured=structured,
    )

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-sections-v9-structured",
        "structured": structured,
    }
