from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key

import re
from urllib.parse import urlparse

router = APIRouter()

# ============================================================
# Helpers
# ============================================================

def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or (url or "")
    except Exception:
        return url or ""

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())

def _has_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def _clip(s: str, n: int = 280) -> str:
    s = s or ""
    return s[:n] + ("…" if len(s) > n else "")

# ============================================================
# Contact extraction
# ============================================================

_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
_URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

def _extract_contacts(text: str) -> dict:
    t = text or ""

    emails = list(dict.fromkeys([e.lower() for e in _EMAIL_RE.findall(t)]))[:6]

    phones_raw = _PHONE_RE.findall(t)
    phones = []
    for p in phones_raw:
        p2 = _normalize_ws(p)
        digits = re.sub(r"\D", "", p2)
        if len(digits) >= 9:
            phones.append(p2)
    phones = list(dict.fromkeys(phones))[:5]

    urls = list(dict.fromkeys(_URL_RE.findall(t)))[:8]

    paths = []
    for m in re.finditer(r"(?i)\b(/(privacy|contact|support|dpo|data-protection|legal|help)[^\s]*)", t):
        paths.append(m.group(1))
    paths = list(dict.fromkeys(paths))[:8]

    return {
        "emails": emails,
        "phones": phones,
        "urls": urls,
        "paths": paths,
    }

# ============================================================
# Jurisdiction signals + detection
# ============================================================

def _jurisdiction_signals(text: str) -> dict:
    t = (text or "").lower()

    gdpr = any(k in t for k in ["gdpr", "european union", "eea", "data controller", "data processor", "supervisory authority"])
    ccpa = any(k in t for k in ["ccpa", "cpra", "california consumer privacy act", "do not sell", "do-not-sell", "sharing personal information"])
    ke = any(k in t for k in [
        "kenya",
        "data protection act, 2019",
        "data protection act 2019",
        "office of the data protection commissioner",
        "odpc",
    ])
    uk = any(k in t for k in ["uk gdpr", "united kingdom", "ico", "information commissioner's office"])
    za = any(k in t for k in ["popia", "south africa", "information regulator"])
    br = any(k in t for k in ["lgpd", "brazil", "autoridade nacional de proteção de dados", "anpd"])

    return {
        "gdpr_mentioned": gdpr,
        "ccpa_mentioned": ccpa,
        "ke_mentioned": ke,
        "uk_mentioned": uk,
        "za_mentioned": za,
        "br_mentioned": br,
    }

def _detect_jurisdiction(text: str) -> str:
    sig = _jurisdiction_signals(text)

    # Order matters: most specific first
    if sig["ke_mentioned"]:
        return "KE"
    if sig["ccpa_mentioned"]:
        return "US-CA"
    if sig["uk_mentioned"]:
        return "UK"
    if sig["za_mentioned"]:
        return "ZA"
    if sig["br_mentioned"]:
        return "BR"
    if sig["gdpr_mentioned"]:
        return "EU"

    # fallback
    t = (text or "").lower()
    if "united states" in t or "u.s." in t or "usa" in t:
        return "US"
    return "UNKNOWN"

# ============================================================
# Sentence extraction + smarter dedupe
# ============================================================

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text or "")
    out: list[str] = []
    for p in parts:
        s = _normalize_ws(p)
        if len(s) < 45:
            continue
        low = s.lower()
        # drop cookie banners / boilerplate fragments
        if low.startswith(("cookie settings", "accept cookies", "all rights reserved", "copyright")):
            continue
        out.append(s)
    return out

def _token_set(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return (inter / union) if union else 0.0

def _dedupe_sentences(sents: list[str], sim_threshold: float = 0.72) -> list[str]:
    kept: list[str] = []
    kept_sets: list[set[str]] = []
    for s in sents:
        ws = _token_set(s)
        duplicate = False
        for ks in kept_sets:
            if _jaccard(ws, ks) >= sim_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(s)
            kept_sets.append(ws)
    return kept

def _find_evidence(text: str, keywords: list[str], max_items: int, used: set[str]) -> list[str]:
    """
    Find up to max_items sentences containing any keyword.
    Uses `used` to prevent the same sentence showing up in many sections.
    """
    sents = _dedupe_sentences(_split_sentences(text))
    out: list[str] = []
    for s in sents:
        sl = s.lower()
        if any(k in sl for k in keywords):
            s2 = _clip(s, 320)
            if s2 in used:
                continue
            used.add(s2)
            out.append(s2)
        if len(out) >= max_items:
            break
    return out

# ============================================================
# Controller extraction
# ============================================================

def _extract_controller_hint(text: str, fallback_domain: str) -> str | None:
    sents = _split_sentences(text or "")

    # strongest: Data Controller / Controller / Data controller:
    for s in sents:
        m = re.search(r"(?i)\b(data\s+controller|controller)[:\s]\s*(.{3,140})", s)
        if m:
            val = _normalize_ws(m.group(2))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:140]

    # next: applies to / operated by
    for s in sents:
        m = re.search(r"(?i)\b(operated by|owned by|this policy applies to)\s+(.{3,140})", s)
        if m:
            val = _normalize_ws(m.group(2))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:140]

    return fallback_domain or None

# ============================================================
# Risk scoring + flags
# ============================================================

def _risk_and_flags(text: str, jurisdiction: str, signals: dict) -> tuple[int, list[str]]:
    t = (text or "").lower()
    flags: list[str] = []
    score = 10

    def hit(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)

    # general risky patterns
    hit(("share" in t or "sharing" in t) and ("third party" in t or "third-party" in t), 12,
        "Shares data with third parties (check scope & opt-out).")
    hit(("sell" in t or "sale" in t) and "personal" in t, 18,
        "Mentions selling personal information (verify opt-out/limits).")
    hit("advertising" in t or "marketing" in t or "targeted" in t, 10,
        "Data used for advertising/marketing (look for opt-out).")
    hit("cookies" in t or "tracking" in t or "analytics" in t or "pixel" in t, 8,
        "Tracking/cookies/analytics mentioned (check consent controls).")
    hit("retain" in t or "retention" in t or "as long as necessary" in t, 8,
        "Retention mentioned (check duration and purpose).")
    hit("arbitration" in t or ("class action" in t and "waive" in t), 14,
        "Arbitration/class-action waiver may limit legal options.")
    hit(("without notice" in t or "at any time" in t) and "change" in t, 10,
        "Policy may change without notice (weak user protections).")
    hit("precise location" in t or "gps" in t or "location data" in t, 10,
        "Collects location data (verify necessity and opt-out).")
    hit("biometric" in t or ("face" in t and "recognition" in t), 16,
        "Biometric/face recognition mentioned (high sensitivity).")
    hit("international" in t and ("transfer" in t or "outside" in t or "cross-border" in t), 8,
        "International data transfers mentioned (check safeguards).")

    # jurisdiction-aware missing basics
    if jurisdiction == "KE":
        purpose = _has_any(t, ["purpose", "how we use", "we use", "for the purpose"])
        if not purpose:
            hit(True, 6, "Kenya: Missing/unclear purpose of processing.")
        complaints = _has_any(t, ["odpc", "office of the data protection commissioner", "complaint"])
        if not complaints:
            hit(True, 4, "Kenya: Missing/unclear ODPC complaint route.")
    if jurisdiction == "US-CA" or signals.get("ccpa_mentioned"):
        dns = _has_any(t, ["do not sell", "do-not-sell"])
        if not dns:
            hit(True, 6, "California: Missing/unclear 'Do Not Sell/Share' notice.")
    if jurisdiction == "EU" or signals.get("gdpr_mentioned"):
        lawful = _has_any(t, ["legal basis", "lawful basis", "legitimate interests", "contract", "consent"])
        if not lawful:
            hit(True, 6, "GDPR: Missing/unclear lawful basis for processing.")

    score = max(0, min(100, score))
    return score, flags[:12]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

# ============================================================
# Tiny structured extraction (rule-based)
# ============================================================

def _extract_structured(text: str, url: str, jurisdiction: str, signals: dict, contacts: dict, controller_hint: str | None) -> dict:
    t = (text or "").lower()

    def yn(keys: list[str]) -> bool:
        return _has_any(t, keys)

    rights = {
        "access": yn(["right of access", "request access", "access request", "access your", "data subject access"]),
        "delete": yn(["delete", "deletion", "erasure", "right to be forgotten", "deletion request"]),
        "correct": yn(["correct", "correction", "rectify", "rectification", "update your"]),
        "object": yn(["object", "objection", "right to object"]),
        "portability": yn(["data portability", "portable copy"]),
        "withdraw_consent": yn(["withdraw consent"]),
        "opt_out_marketing": yn(["opt out", "opt-out", "unsubscribe", "marketing emails", "stop receiving"]),
    }

    sharing = {
        "third_parties": yn(["third party", "third-party", "share with", "shared with"]),
        "service_providers": yn(["service provider", "vendor", "processor", "sub-processor"]),
        "sale_of_data": yn(["sell personal", "selling personal", "do not sell", "do-not-sell"]),
    }

    retention = {
        "mentions_retention": yn(["retain", "retention", "keep for", "as long as necessary", "storage duration"]),
        "mentions_duration": yn(["for a period", "until no longer", "as long as necessary", "for x years", "for x months"]),
    }

    tracking = {
        "cookies_or_tracking": yn(["cookie", "tracking", "analytics", "pixel", "sdk"]),
        "ads_marketing": yn(["advertising", "marketing", "targeted ads", "personalized ads"]),
    }

    cross_border = {
        "international_transfers": yn(["international transfer", "cross-border", "outside", "other countries", "transfer your data"]),
        "safeguards": yn(["standard contractual clauses", "scc", "adequacy", "safeguards"]),
    }

    legal = {
        "arbitration": yn(["arbitration"]),
        "class_action_waiver": yn(["class action", "waive", "waiver"]),
    }

    complaints = {
        "mentions_supervisory_authority": yn(["supervisory authority", "lodge a complaint", "complaint to"]),
        "mentions_odpc": yn(["odpc", "office of the data protection commissioner"]),
        "mentions_ico": yn(["ico", "information commissioner's office"]),
    }

    # Kenya checklist
    ke = {
        "controller_identified": bool(controller_hint) or yn(["data controller", "controller", "data processor", "processor"]),
        "contact_provided": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")) or yn(["contact", "privacy@", "dpo"]),
        "rights_mentioned": any(rights.values()) or yn(["data subject"]),
        "purpose_mentioned": yn(["purpose", "how we use", "we use", "for the purpose"]),
        "retention_mentioned": retention["mentions_retention"],
        "sharing_disclosed": sharing["third_parties"] or sharing["service_providers"],
        "cross_border_mentioned": cross_border["international_transfers"],
        "complaints_route": complaints["mentions_odpc"] or yn(["complaint"]),
    }

    # GDPR checklist (show even if jurisdiction UNKNOWN but GDPR mentioned)
    gdpr = {
        "controller_processor_role": yn(["controller", "processor", "data controller", "data processor"]),
        "lawful_basis": yn(["legal basis", "lawful basis", "legitimate interests", "consent", "contract", "legal obligation"]),
        "rights_listed": any(rights.values()) or yn(["data subject rights"]),
        "dpo_or_contact": yn(["dpo", "data protection officer"]) or bool(contacts.get("emails") or contacts.get("paths")),
        "retention_disclosed": retention["mentions_retention"],
        "international_transfers": cross_border["international_transfers"],
        "supervisory_authority": complaints["mentions_supervisory_authority"],
    }

    # US-CA checklist
    usca = {
        "categories_collected": yn(["categories of personal information", "we collect the following categories"]),
        "purposes": yn(["purpose", "how we use", "we use", "for the purpose"]),
        "do_not_sell_share": yn(["do not sell", "do-not-sell", "do not sell or share", "do not share"]),
        "rights_to_know_delete": rights["access"] or rights["delete"] or yn(["right to know", "right to delete"]),
        "sensitive_pi_notice": yn(["sensitive personal information", "sensitive personal"]),
        "opt_out_ads": yn(["opt out of targeted advertising", "limit the use of my sensitive"]),
    }

    return {
        "site": _domain_from_url(url),
        "jurisdiction": jurisdiction,
        "signals": signals,
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
        "cross_border": cross_border,
        "legal": legal,
        "complaints": complaints,
        "ke_checklist": ke,
        "gdpr_checklist": gdpr,
        "usca_checklist": usca,
    }

# ============================================================
# Summary builder: sections + dynamic recommendations
# ============================================================

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _coverage_percent(section_evidence: dict[str, list[str]]) -> int:
    keys = ["collected", "used", "sharing", "retention", "rights", "security", "transfers", "contact"]
    have = sum(1 for k in keys if section_evidence.get(k))
    return int(round((have / len(keys)) * 100))

def _missing_topics(section_evidence: dict[str, list[str]]) -> list[str]:
    mapping = [
        ("Data collected", "collected"),
        ("How data is used", "used"),
        ("Sharing & third parties", "sharing"),
        ("Retention", "retention"),
        ("Your rights & choices", "rights"),
        ("Security", "security"),
        ("International transfers", "transfers"),
        ("Contact / complaints", "contact"),
    ]
    miss = [label for (label, k) in mapping if not section_evidence.get(k)]
    return miss

def _top_concern(red_flags: list[str]) -> str:
    if not red_flags:
        return "None detected."
    return red_flags[0]

def _dynamic_recommendations(level: str, jurisdiction: str, signals: dict, structured: dict) -> list[str]:
    lvl = (level or "").upper()

    recs: list[str] = []

    # Risk-based (always present)
    if lvl == "HIGH":
        recs += [
            "Avoid accepting unless you really need this service; look for alternatives.",
            "Check if the policy allows **selling/sharing** data and whether you can opt out.",
            "Look for **retention duration** and **deletion request** steps before proceeding.",
        ]
    elif lvl == "MEDIUM":
        recs += [
            "Proceed with caution: confirm what’s collected + why it’s collected.",
            "Check opt-out options for marketing/tracking and third-party sharing.",
            "Find the contact/DPO route for access/delete requests.",
        ]
    else:
        recs += [
            "Low risk overall, but confirm what’s collected and whether tracking is optional.",
            "Check how to opt out of marketing emails/cookies if you prefer.",
        ]

    # Jurisdiction-based (conditional)
    gdpr_show = (jurisdiction == "EU") or bool(signals.get("gdpr_mentioned"))
    usca_show = (jurisdiction == "US-CA") or bool(signals.get("ccpa_mentioned"))
    ke_show = (jurisdiction == "KE")

    if gdpr_show:
        gdpr = structured.get("gdpr_checklist", {})
        if not gdpr.get("lawful_basis"):
            recs.append("GDPR: Look for the **lawful basis** (consent/contract/legitimate interests).")
        if not gdpr.get("supervisory_authority"):
            recs.append("GDPR: Check how to **lodge a complaint** with a supervisory authority.")
        if not gdpr.get("international_transfers"):
            recs.append("GDPR: Confirm whether data is transferred outside the EU/EEA and what safeguards apply (SCCs).")

    if usca_show:
        usca = structured.get("usca_checklist", {})
        if not usca.get("do_not_sell_share"):
            recs.append("California: Look for a **“Do Not Sell or Share”** link/notice.")
        if not usca.get("rights_to_know_delete"):
            recs.append("California: Confirm you can **request access/know** and **delete** data.")

    if ke_show:
        ke = structured.get("ke_checklist", {})
        if not ke.get("purpose_mentioned"):
            recs.append("Kenya: Confirm the **purpose of processing** is clearly stated.")
        if not ke.get("complaints_route"):
            recs.append("Kenya: Ensure there’s a complaints route via **ODPC** (Office of the Data Protection Commissioner).")

    # Small cap: keep UI tidy
    return recs[:9]

def _format_checklist(title: str, items: dict[str, bool]) -> str:
    lines = [f"### {title}"]
    for k, v in items.items():
        mark = "✅" if v else "❌"
        label = k.replace("_", " ").strip()
        lines.append(f"- {mark} {label}")
    return "\n".join(lines) + "\n"

def _build_summary(url: str, text: str, jurisdiction: str, level: str, score: int, red_flags: list[str], signals: dict, structured: dict) -> str:
    used: set[str] = set()

    # Evidence sections (avoid repeating the same sentence everywhere)
    collected = _find_evidence(text, ["information we collect", "data we collect", "we collect", "personal information", "account information"], 3, used)
    used_e = _find_evidence(text, ["how we use", "we use", "purpose", "to provide", "improve", "process"], 3, used)
    sharing = _find_evidence(text, ["share", "sharing", "third party", "third-party", "service provider", "vendor", "processor"], 3, used)
    retention = _find_evidence(text, ["retain", "retention", "as long as necessary", "keep for", "storage duration"], 2, used)
    rights = _find_evidence(text, ["your rights", "right to", "access", "delete", "erasure", "opt out", "opt-out", "unsubscribe", "rectify", "correct"], 3, used)
    security = _find_evidence(text, ["security", "safeguard", "protect", "encryption", "secure"], 2, used)
    transfers = _find_evidence(text, ["international", "cross-border", "outside", "transfer", "other countries", "eea"], 2, used)
    contact = _find_evidence(text, ["contact", "email", "dpo", "data protection officer", "complaint", "odpc", "supervisory authority", "ico"], 2, used)

    sec_map = {
        "collected": collected,
        "used": used_e,
        "sharing": sharing,
        "retention": retention,
        "rights": rights,
        "security": security,
        "transfers": transfers,
        "contact": contact,
    }

    domain = _domain_from_url(url)
    coverage = _coverage_percent(sec_map)
    missing = _missing_topics(sec_map)
    top = _top_concern(red_flags)

    # Contacts summary
    emails = (structured.get("contacts", {}) or {}).get("emails", []) or []
    phones = (structured.get("contacts", {}) or {}).get("phones", []) or []
    contact_bits = []
    if emails:
        contact_bits.append(f"email: {', '.join(emails[:2])}")
    if phones:
        contact_bits.append(f"phone: {', '.join(phones[:1])}")

    controller_hint = structured.get("controller_hint") or domain
    recs = _dynamic_recommendations(level, jurisdiction, signals, structured)

    out: list[str] = []
    out.append("## Policy Summary")
    out.append(f"**Site:** {domain}\n")

    out.append("### Key takeaways")
    out.append(f"- Risk level: **{level}** (score **{score}/100**)")
    # IMPORTANT: show EU checklist trigger even if jurisdiction unknown but GDPR mentioned
    juris_label = jurisdiction
    if jurisdiction == "UNKNOWN" and signals.get("gdpr_mentioned"):
        juris_label = "UNKNOWN (GDPR mentioned)"
    if jurisdiction == "UNKNOWN" and signals.get("ccpa_mentioned"):
        juris_label = "UNKNOWN (CCPA/CPRA mentioned)"
    out.append(f"- Jurisdiction signals: **{juris_label}**")
    out.append(f"- Coverage: **{coverage}%** of key topics detected")
    if missing:
        out.append(f"- Missing/unclear: " + ", ".join([f"**{m}**" for m in missing[:6]]) + ("…" if len(missing) > 6 else ""))
    if red_flags:
        out.append(f"- Top concern: {top}")
    out.append(f"- Likely controller/org: **{_normalize_ws(controller_hint)}**")
    if contact_bits:
        out.append(f"- Contact hints: " + "; ".join(contact_bits))

    out.append("\n### Recommendations (based on risk + jurisdiction)")
    for r in recs:
        out.append(f"- {r}")

    # Checklists (show conditionally)
    # Kenya
    if jurisdiction == "KE":
        out.append("\n" + _format_checklist("Kenya checklist", structured.get("ke_checklist", {})))

    # GDPR checklist: show if EU OR GDPR mentioned (even if UNKNOWN)
    if jurisdiction == "EU" or signals.get("gdpr_mentioned"):
        out.append("\n" + _format_checklist("EU GDPR checklist", structured.get("gdpr_checklist", {})))

    # US-CA checklist: show if US-CA OR CCPA mentioned
    if jurisdiction == "US-CA" or signals.get("ccpa_mentioned"):
        out.append("\n" + _format_checklist("US-CA (CCPA/CPRA) checklist", structured.get("usca_checklist", {})))

    # Evidence sections
    out.append("\n### Evidence by section\n")
    out.append(_mk_section("Data collected", collected))
    out.append(_mk_section("How data is used", used_e))
    out.append(_mk_section("Sharing & third parties", sharing))
    out.append(_mk_section("Retention", retention))
    out.append(_mk_section("Your rights & choices", rights))
    out.append(_mk_section("Security", security))
    out.append(_mk_section("International transfers", transfers))
    out.append(_mk_section("Contact / complaints", contact))

    return "\n".join(out).strip()

# ============================================================
# Endpoint
# ============================================================

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "")[:20000]

    signals = _jurisdiction_signals(text)
    jurisdiction = _detect_jurisdiction(text)

    domain = _domain_from_url(url)
    contacts = _extract_contacts(text)
    controller_hint = _extract_controller_hint(text, domain)

    score, red_flags = _risk_and_flags(text, jurisdiction, signals)
    level = _risk_level(score)

    structured = _extract_structured(text, url, jurisdiction, signals, contacts, controller_hint)
    summary = _build_summary(
        url=url,
        text=text,
        jurisdiction=jurisdiction,
        level=level,
        score=score,
        red_flags=red_flags,
        signals=signals,
        structured=structured,
    )

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,  # markdown
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-v10-juris-engine",
        "structured": structured,  # tiny rule-based extraction payload
    }
