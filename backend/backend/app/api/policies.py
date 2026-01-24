from fastapi import APIRouter, Request, Depends
from backend.app.core.limiter import limiter, tier_limit_provider
from backend.app.core.auth import require_api_key
import re
from urllib.parse import urlparse

router = APIRouter()

# ============================================================
# Basics
# ============================================================

def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower() or (url or "")
    except Exception:
        return url or ""

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())

def _t(text: str) -> str:
    return (text or "").lower()

def _has_any(text_lower: str, phrases: list[str]) -> bool:
    return any(p in text_lower for p in phrases)

# ============================================================
# Contact extraction (emails / phones / common paths)
# ============================================================

_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
_URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

def _extract_contacts(text: str) -> dict:
    raw = text or ""
    emails = list(dict.fromkeys([e.lower() for e in _EMAIL_RE.findall(raw)]))[:5]

    phones = []
    for p in _PHONE_RE.findall(raw):
        p2 = _normalize_ws(p)
        if len(re.sub(r"\D", "", p2)) >= 9:
            phones.append(p2)
    phones = list(dict.fromkeys(phones))[:4]

    urls = list(dict.fromkeys(_URL_RE.findall(raw)))[:6]

    paths = []
    for m in re.finditer(r"(?i)\b(/(privacy|contact|support|dpo|data-protection|legal|preferences|opt-?out)[^\s]*)", raw):
        paths.append(m.group(1))
    paths = list(dict.fromkeys(paths))[:8]

    return {"emails": emails, "phones": phones, "urls": urls, "paths": paths}

# ============================================================
# Sentence extraction + evidence selection (fast, rule-based)
# ============================================================

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text or "")
    out = []
    for p in parts:
        s = _normalize_ws(p)
        if len(s) < 45:
            continue
        low = s.lower()
        if low.startswith(("cookie settings", "accept cookies", "all rights reserved", "copyright")):
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
    kept = []
    kept_sets = []
    for s in sents:
        ws = set(re.findall(r"[a-z0-9]+", s.lower()))
        dup = False
        for ks in kept_sets:
            if _jaccard(ws, ks) >= sim_threshold:
                dup = True
                break
        if not dup:
            kept.append(s)
            kept_sets.append(ws)
    return kept

def _find_evidence(
    text: str,
    include: list[str],
    exclude: list[str] | None = None,
    max_items: int = 3,
    used: set[str] | None = None,
) -> list[str]:
    """
    Evidence selection tuned per section:
    - Must match at least one 'include' phrase.
    - Must NOT match any 'exclude' phrase (if provided).
    - Avoid repeats across sections using 'used' set.
    """
    used = used or set()
    exclude = exclude or []

    sents = _dedupe_sentences(_split_sentences(text))
    out = []

    for s in sents:
        sl = s.lower()

        if include and not any(k in sl for k in include):
            continue
        if exclude and any(bad in sl for bad in exclude):
            continue

        s2 = s[:280] + ("…" if len(s) > 280 else "")
        if s2 in used:
            continue

        out.append(s2)
        used.add(s2)

        if len(out) >= max_items:
            break

    return out

# ============================================================
# Jurisdiction detection + special "signals" flags
# ============================================================

def _detect_jurisdiction(text: str) -> str:
    tl = _t(text)

    if _has_any(tl, ["gdpr", "general data protection regulation", "article 6", "article 13", "article 14", "eea", "european union"]):
        return "EU"

    if _has_any(tl, ["ccpa", "cpra", "california consumer privacy act", "do not sell", "do not share", "global privacy control", "gpc"]):
        return "US-CA"

    if _has_any(tl, ["uk gdpr", "ico", "united kingdom", "information commissioner's office"]):
        return "UK"

    if _has_any(tl, ["kenya", "data protection act 2019", "data protection act, 2019", "office of the data protection commissioner", "odpc"]):
        return "KE"

    if _has_any(tl, ["popia", "south africa", "information regulator"]):
        return "ZA"

    if _has_any(tl, ["lgpd", "brazil"]):
        return "BR"

    if _has_any(tl, ["united states", "u.s.", "usa"]):
        return "US"

    return "UNKNOWN"

def _gdpr_mentioned(text: str) -> bool:
    tl = _t(text)
    return _has_any(tl, ["gdpr", "general data protection regulation", "article 6", "lawful basis", "controller", "processor", "data subject"])

# ============================================================
# Risk + red flags (simple, additive)
# ============================================================

def _risk_and_flags(text: str, structured: dict) -> tuple[int, list[str]]:
    tl = _t(text)
    flags: list[str] = []
    score = 10

    def hit(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            flags.append(msg)

    hit(structured["sharing"]["third_parties"], 12, "Shares data with third parties (check scope & opt-out).")
    hit(structured["sharing"]["sale_of_data"], 18, "Mentions selling/sharing personal info (verify opt-out).")
    hit(structured["tracking"]["ads_marketing"], 8, "Data used for advertising/marketing (look for opt-out).")
    hit(structured["tracking"]["cookies_or_tracking"], 6, "Tracking/cookies/analytics mentioned (check consent controls).")

    hit(structured["retention"]["mentions_retention"], 8, "Retention mentioned (check duration and purpose).")
    hit(structured["retention"]["mentions_duration"] is False and structured["retention"]["mentions_retention"], 6, "Retention mentioned but duration unclear.")

    hit(_has_any(tl, ["precise location", "gps", "location data"]), 10, "Collects location data (verify necessity and controls).")
    hit(_has_any(tl, ["biometric", "face recognition", "fingerprint"]), 16, "Biometric data mentioned (high sensitivity).")
    hit(_has_any(tl, ["children", "kids", "under 13", "minor"]), 10, "Children/minors mentioned (extra compliance obligations).")

    hit(_has_any(tl, ["arbitration"]), 14, "Arbitration clause may limit legal options.")
    hit(_has_any(tl, ["class action", "waive", "waiver"]), 12, "Class-action waiver may limit legal options.")
    hit(_has_any(tl, ["without notice"]) and _has_any(tl, ["change", "update"]), 10, "Policy may change without notice (weak user protections).")

    hit(structured["cross_border"]["international_transfers"], 8, "International data transfers mentioned (check safeguards).")

    j = structured["jurisdiction"]
    if j == "KE":
        ke = structured["ke_checklist"]
        if not ke["purpose_mentioned"]:
            flags.append("Kenya: Missing/unclear purpose of processing.")
            score += 8
        if not ke["complaints_route"]:
            flags.append("Kenya: Missing ODPC complaint route.")
            score += 6

    if j == "EU" or structured.get("gdpr_signal", False):
        eu = structured["eu_checklist"]
        if not eu["lawful_basis_article6"]:
            flags.append("GDPR: Missing/unclear lawful basis (Article 6).")
            score += 8
        if not eu["dpo_or_contact"]:
            flags.append("GDPR: Missing DPO/contact details.")
            score += 6

    if j == "US-CA":
        ca = structured["usca_checklist"]
        if not ca["do_not_sell_or_share"]:
            flags.append("US-CA: Missing 'Do Not Sell/Share' notice/link.")
            score += 8
        if not ca["gpc_honored"]:
            flags.append("US-CA: Missing/unclear Global Privacy Control (GPC) support.")
            score += 6

    score = max(0, min(100, score))
    return score, flags[:12]

def _risk_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"

# ============================================================
# Controller hint extraction
# ============================================================

def _extract_controller_hint(text: str, fallback_domain: str) -> str | None:
    sents = _split_sentences(text or "")

    for s in sents:
        m = re.search(r"(?i)\bdata controller[:\s]\s*(.{3,120})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:120]

    for s in sents:
        m = re.search(r"(?i)\bcontroller[:\s]\s*(.{3,120})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:120]

    for s in sents:
        m = re.search(r"(?i)\bthis policy applies to\s+(.{3,120})", s)
        if m:
            val = _normalize_ws(m.group(1))
            val = re.split(r"[.;\n]", val)[0].strip()
            return val[:120]

    return fallback_domain or None

# ============================================================
# Tiny structured extraction (rule-based)
# ============================================================

def _extract_structured(text: str, url: str, jurisdiction: str, contacts: dict, controller_hint: str | None) -> dict:
    tl = _t(text)
    domain = _domain_from_url(url)

    def yn(keys: list[str]) -> bool:
        return _has_any(tl, keys)

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
        "sale_of_data": yn(["sell personal", "selling personal", "do not sell", "do not share"]),
    }

    retention = {
        "mentions_retention": yn(["retain", "retention", "keep for", "as long as necessary", "store for"]),
        "mentions_duration": yn(["for a period", "for x years", "for years", "for months", "until no longer", "as long as necessary"]),
    }

    tracking = {
        "cookies_or_tracking": yn(["cookie", "tracking", "analytics", "pixel", "sdk"]),
        "ads_marketing": yn(["advertising", "marketing", "targeted ads", "personalized ads", "interest-based"]),
    }

    cross_border = {
        "international_transfers": yn(["international transfer", "cross-border", "outside", "other countries", "stored in"]),
        "safeguards": yn(["standard contractual clauses", "scc", "adequacy", "safeguards"]),
    }

    complaints = {
        "mentions_supervisory_authority": yn(["supervisory authority", "lodge a complaint", "complaint to"]),
        "mentions_odpc": yn(["odpc", "office of the data protection commissioner"]),
        "mentions_ico": yn(["ico", "information commissioner's office"]),
    }

    usca = {
        "do_not_sell_or_share": yn(["do not sell", "do not share", "do not sell or share"]),
        "gpc_honored": yn(["global privacy control", "gpc"]),
        "notice_at_collection": yn(["notice at collection", "categories of personal information"]),
        "sensitive_pi": yn(["sensitive personal information", "spi"]),
        "limit_sensitive_use": yn(["limit the use", "limit use of sensitive", "limit the use of sensitive personal information"]),
    }

    eu = {
        "lawful_basis_article6": yn(["article 6", "lawful basis", "legitimate interests", "contract", "legal obligation", "consent", "vital interests", "public task"]),
        "dpo_or_contact": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")) or yn(["dpo", "data protection officer", "contact us"]),
        "rights_listed": any(rights.values()) or yn(["data subject"]),
        "retention_stated": retention["mentions_retention"],
        "transfers_safeguards": cross_border["international_transfers"] and cross_border["safeguards"],
        "supervisory_authority": complaints["mentions_supervisory_authority"] or yn(["supervisory authority"]),
    }

    ke = {
        "controller_identified": bool(controller_hint) or yn(["data controller", "controller", "data processor", "processor"]),
        "contact_provided": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")) or yn(["contact", "privacy@", "dpo"]),
        "rights_mentioned": any(rights.values()) or yn(["data subject"]),
        "purpose_mentioned": yn(["purpose", "how we use", "we use", "for the purpose", "used to"]),
        "retention_mentioned": retention["mentions_retention"],
        "sharing_disclosed": sharing["third_parties"] or sharing["service_providers"],
        "cross_border_mentioned": cross_border["international_transfers"],
        "complaints_route": complaints["mentions_odpc"] or yn(["office of the data protection commissioner", "odpc", "complaint"]),
    }

    return {
        "site": domain,
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
        "cross_border": cross_border,
        "complaints": complaints,
        "usca_checklist": usca,
        "eu_checklist": eu,
        "ke_checklist": ke,
        "gdpr_signal": _gdpr_mentioned(text),
    }

# ============================================================
# Coverage score
# ============================================================

def _coverage(structured: dict) -> float:
    present = 0
    total = 8

    if any(structured["rights"].values()): present += 1
    if structured["sharing"]["third_parties"] or structured["sharing"]["service_providers"]: present += 1
    if structured["retention"]["mentions_retention"]: present += 1
    if structured["tracking"]["cookies_or_tracking"] or structured["tracking"]["ads_marketing"]: present += 1
    if structured["cross_border"]["international_transfers"]: present += 1
    if structured["controller_hint"]: present += 1
    if structured["contacts"]["emails"] or structured["contacts"]["phones"] or structured["contacts"]["paths"]: present += 1
    if structured["jurisdiction"] != "UNKNOWN" or structured.get("gdpr_signal", False): present += 1

    return present / total

# ============================================================
# Dynamic recommendations (risk + jurisdiction)
# ============================================================

def _dynamic_recommendations(level: str, score: int, structured: dict) -> list[str]:
    recs: list[str] = []
    j = structured["jurisdiction"]
    gdpr_signal = structured.get("gdpr_signal", False)

    if level == "HIGH":
        recs.append("Before accepting: look for an **opt-out / delete request** process and verify it works.")
        recs.append("Avoid giving **sensitive info** (ID, precise location, biometrics) unless truly necessary.")
    elif level == "MEDIUM":
        recs.append("Review **sharing/tracking** sections and confirm how to opt out (ads, cookies).")
    else:
        recs.append("Low risk overall — still confirm what’s collected and whether you can opt out of tracking/marketing.")

    if structured["tracking"]["cookies_or_tracking"] and not structured["rights"]["opt_out_marketing"]:
        recs.append("Check for **cookie preferences** or consent banner controls (analytics/ads).")
    if structured["retention"]["mentions_retention"] and not structured["retention"]["mentions_duration"]:
        recs.append("Retention is mentioned but vague — look for an actual **time period** or deletion schedule.")

    if j == "KE":
        ke = structured["ke_checklist"]
        recs.append("Kenya: Confirm they reference the **Data Protection Act, 2019** and name the **data controller**.")
        if not ke["purpose_mentioned"]:
            recs.append("Kenya: Ask for the **purpose** of processing (why they collect each category).")
        if not ke["complaints_route"]:
            recs.append("Kenya: Look for an **ODPC complaint route** (Office of the Data Protection Commissioner).")
        else:
            recs.append("Kenya: If unresolved, you can complain via **ODPC** — search “ODPC Kenya lodge complaint”.")
        if not ke["rights_mentioned"]:
            recs.append("Kenya: Ensure your rights are stated (access/correction/deletion/objection).")

    if j == "EU" or gdpr_signal:
        eu = structured["eu_checklist"]
        recs.append("GDPR: Look for the **lawful basis** for each purpose (Article 6: consent/contract/legal obligation/legitimate interests).")
        if not eu["lawful_basis_article6"]:
            recs.append("GDPR: Missing lawful basis — policy should state **why** processing is lawful (Article 6).")
        if not eu["supervisory_authority"]:
            recs.append("GDPR: Policy should mention your right to complain to a **supervisory authority**.")
        if not eu["transfers_safeguards"] and structured["cross_border"]["international_transfers"]:
            recs.append("GDPR: If data leaves the EEA, look for safeguards like **SCCs** or adequacy decision.")
        if not eu["dpo_or_contact"]:
            recs.append("GDPR: Look for a **DPO/contact** email or address for privacy requests.")

    if j == "US-CA":
        ca = structured["usca_checklist"]
        recs.append("US-CA: Look for a **“Do Not Sell or Share My Personal Information”** link.")
        recs.append("US-CA: Check whether they honor **Global Privacy Control (GPC)** signals.")
        if not ca["do_not_sell_or_share"]:
            recs.append("US-CA: Missing Do Not Sell/Share — that’s a red flag under CCPA/CPRA expectations.")
        if not ca["gpc_honored"]:
            recs.append("US-CA: If you use GPC, confirm they explicitly say they honor it.")
        if ca["sensitive_pi"] and not ca["limit_sensitive_use"]:
            recs.append("US-CA: If they collect sensitive PI, look for **“Limit the Use of Sensitive Personal Information.”**")

    if j == "UNKNOWN" and not gdpr_signal:
        recs.append("No clear jurisdiction detected — check if they mention local regulators, rights, or complaint channels.")
        if structured["contacts"]["emails"]:
            recs.append("Use their privacy email to request: categories collected, purposes, sharing, retention period, and deletion steps.")

    return recs[:8]

# ============================================================
# Summary builder (markdown) — smarter evidence selection
# ============================================================

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

# Section include/exclude tuning:
# Goal: prevent “opt out / unsubscribe” lines from showing up under “Data collected”
_EVID_EXCLUDE_COMMON = [
    "all rights reserved",
    "cookie settings",
    "accept cookies",
]

_EVID_EXCLUDE_DATA_COLLECTED = _EVID_EXCLUDE_COMMON + [
    "opt out", "opt-out", "unsubscribe",
    "marketing", "advertising",
    "do not sell", "do not share", "gpc", "global privacy control",
    "retain", "retention", "as long as necessary",
    "contact", "complaint", "odpc", "supervisory authority",
]

_EVID_EXCLUDE_RETENTION = _EVID_EXCLUDE_COMMON + [
    "opt out", "opt-out", "unsubscribe",
    "contact", "complaint",
]

_EVID_EXCLUDE_TRANSFERS = _EVID_EXCLUDE_COMMON + [
    "opt out", "opt-out", "unsubscribe",
    "contact", "complaint",
]

_EVID_EXCLUDE_SECURITY = _EVID_EXCLUDE_COMMON + [
    "opt out", "opt-out", "unsubscribe",
    "contact", "complaint",
]

def _build_summary(url: str, text: str, structured: dict, score: int, level: str, red_flags: list[str]) -> str:
    used: set[str] = set()
    domain = structured["site"]

    # Data collected: ONLY collection/category/type wording
    collected = _find_evidence(
        text,
        include=[
            "we collect", "information we collect", "data we collect", "personal information we collect",
            "categories of personal information", "the categories of", "information you provide",
            "account information", "device information", "log data", "usage data",
            "payment information", "billing", "location data", "biometric", "identifiers",
        ],
        exclude=_EVID_EXCLUDE_DATA_COLLECTED,
        max_items=3,
        used=used,
    )

    # How used: purpose/processing wording
    used2 = _find_evidence(
        text,
        include=[
            "we use", "how we use", "used to", "for the purpose", "purpose of processing",
            "to provide", "to improve", "to operate", "to personalize", "to communicate",
            "legal obligation", "legitimate interests", "consent", "contract",
        ],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=3,
        used=used,
    )

    # Sharing: disclosure wording (allow opt-out-of-sharing lines to stay here)
    sharing = _find_evidence(
        text,
        include=[
            "we share", "share with", "sharing", "third party", "third-party",
            "service provider", "vendor", "processor", "sub-processor", "affiliates",
            "partners", "disclose", "disclosure",
            "do not sell", "do not share",  # ok for sharing section
        ],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=3,
        used=used,
    )

    # Retention: retention wording
    retention = _find_evidence(
        text,
        include=["retain", "retention", "keep for", "as long as necessary", "store for", "storage period"],
        exclude=_EVID_EXCLUDE_RETENTION,
        max_items=2,
        used=used,
    )

    # Rights: rights/requests/opt-out wording (THIS is where opt-out belongs)
    rights = _find_evidence(
        text,
        include=[
            "your rights", "rights you have", "access", "delete", "erasure",
            "rectify", "correction", "opt out", "opt-out", "unsubscribe",
            "object", "withdraw consent", "data portability",
            "do not sell", "do not share", "global privacy control", "gpc",
        ],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=3,
        used=used,
    )

    # Security: security/encryption wording
    security = _find_evidence(
        text,
        include=["security", "safeguard", "protect", "encryption", "secure", "measures", "access controls"],
        exclude=_EVID_EXCLUDE_SECURITY,
        max_items=2,
        used=used,
    )

    # Transfers: international/cross-border wording
    transfers = _find_evidence(
        text,
        include=["international", "transfer", "cross-border", "outside", "other countries", "eea", "stored in"],
        exclude=_EVID_EXCLUDE_TRANSFERS,
        max_items=2,
        used=used,
    )

    # Contact/complaints: contact/regulator wording
    contact = _find_evidence(
        text,
        include=["contact", "email", "privacy@", "dpo", "data protection officer", "odpc", "complaint", "supervisory authority"],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=2,
        used=used,
    )

    cov = _coverage(structured)
    missing_topics = []
    if not collected: missing_topics.append("Data collected")
    if not used2: missing_topics.append("How data is used")
    if not sharing: missing_topics.append("Sharing & third parties")
    if not retention: missing_topics.append("Retention")
    if not rights: missing_topics.append("Your rights & choices")

    top_concern = "None detected"
    if red_flags:
        top_concern = red_flags[0].rstrip(".")

    recs = _dynamic_recommendations(level, score, structured)

    j = structured["jurisdiction"]
    j_display = j
    if j == "UNKNOWN" and structured.get("gdpr_signal", False):
        j_display = "UNKNOWN (GDPR mentioned)"

    lines = []
    lines.append("## Policy Summary")
    lines.append(f"**Site:** {domain}")
    lines.append("")
    lines.append("### Key takeaways")
    lines.append(f"- Risk level: **{level}** (score **{score}/100**)")
    lines.append(f"- Jurisdiction signals: **{j_display}**")
    lines.append(f"- Coverage: **{int(round(cov*100))}%** of key topics detected")
    if missing_topics:
        lines.append(f"- Missing/unclear: **{', '.join(missing_topics[:6])}**")
    lines.append(f"- Top concern: {top_concern}")
    if structured.get("controller_hint"):
        lines.append(f"- Likely controller/org: **{structured['controller_hint']}**")
    if structured["contacts"]["emails"]:
        lines.append(f"- Contact hints: email: {', '.join(structured['contacts']['emails'][:2])}")
    lines.append("")
    lines.append("### What to do next")
    for r in recs:
        lines.append(f"- {r}")

    if structured["jurisdiction"] == "KE":
        ke = structured["ke_checklist"]
        lines.append("")
        lines.append("### Kenya checklist")
        lines.append("- ✅/❌ based on what was detected in the first 20k characters.")
        lines.append(f"- {'✅' if ke['controller_identified'] else '❌'} Controller identified")
        lines.append(f"- {'✅' if ke['contact_provided'] else '❌'} Contact provided")
        lines.append(f"- {'✅' if ke['rights_mentioned'] else '❌'} Rights mentioned")
        lines.append(f"- {'✅' if ke['purpose_mentioned'] else '❌'} Purpose mentioned")
        lines.append(f"- {'✅' if ke['retention_mentioned'] else '❌'} Retention mentioned")
        lines.append(f"- {'✅' if ke['sharing_disclosed'] else '❌'} Sharing disclosed")
        lines.append(f"- {'✅' if ke['cross_border_mentioned'] else '❌'} Cross-border mentioned")
        lines.append(f"- {'✅' if ke['complaints_route'] else '❌'} ODPC / complaints route")

    if structured["jurisdiction"] == "EU" or structured.get("gdpr_signal", False):
        eu = structured["eu_checklist"]
        lines.append("")
        lines.append("### EU GDPR checklist")
        lines.append("- ✅/❌ based on what was detected in the first 20k characters.")
        lines.append(f"- {'✅' if eu['lawful_basis_article6'] else '❌'} Lawful basis stated (Article 6 signals)")
        lines.append(f"- {'✅' if eu['rights_listed'] else '❌'} Data subject rights mentioned")
        lines.append(f"- {'✅' if eu['dpo_or_contact'] else '❌'} DPO/contact provided")
        lines.append(f"- {'✅' if eu['retention_stated'] else '❌'} Retention stated")
        if structured["cross_border"]["international_transfers"]:
            lines.append(f"- {'✅' if eu['transfers_safeguards'] else '❌'} Transfers safeguards (SCCs/adequacy) if cross-border")
        lines.append(f"- {'✅' if eu['supervisory_authority'] else '❌'} Supervisory authority complaint route")

    if structured["jurisdiction"] == "US-CA":
        ca = structured["usca_checklist"]
        lines.append("")
        lines.append("### US-CA (CCPA/CPRA) checklist")
        lines.append("- ✅/❌ based on what was detected in the first 20k characters.")
        lines.append(f"- {'✅' if ca['notice_at_collection'] else '❌'} Notice at collection / categories")
        lines.append(f"- {'✅' if ca['do_not_sell_or_share'] else '❌'} “Do Not Sell/Share” option")
        lines.append(f"- {'✅' if ca['gpc_honored'] else '❌'} GPC honored")
        if ca["sensitive_pi"]:
            lines.append(f"- {'✅' if ca['limit_sensitive_use'] else '❌'} Limit use of sensitive PI (if collected)")

    lines.append("")
    lines.append("### Evidence by section")
    lines.append("")
    lines.append(_mk_section("Data collected", collected))
    lines.append(_mk_section("How data is used", used2))
    lines.append(_mk_section("Sharing & third parties", sharing))
    lines.append(_mk_section("Retention", retention))
    lines.append(_mk_section("Your rights & choices", rights))
    lines.append(_mk_section("Security", security))
    lines.append(_mk_section("International transfers", transfers))
    lines.append(_mk_section("Contact / complaints", contact))

    return "\n".join(lines).strip()

# ============================================================
# Coverage score
# ============================================================

def _coverage(structured: dict) -> float:
    present = 0
    total = 8

    if any(structured["rights"].values()): present += 1
    if structured["sharing"]["third_parties"] or structured["sharing"]["service_providers"]: present += 1
    if structured["retention"]["mentions_retention"]: present += 1
    if structured["tracking"]["cookies_or_tracking"] or structured["tracking"]["ads_marketing"]: present += 1
    if structured["cross_border"]["international_transfers"]: present += 1
    if structured["controller_hint"]: present += 1
    if structured["contacts"]["emails"] or structured["contacts"]["phones"] or structured["contacts"]["paths"]: present += 1
    if structured["jurisdiction"] != "UNKNOWN" or structured.get("gdpr_signal", False): present += 1

    return present / total

# ============================================================
# Endpoint
# ============================================================

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

    score, red_flags = _risk_and_flags(text, structured)
    level = _risk_level(score)

    summary = _build_summary(url, text, structured, score, level, red_flags)

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,
        "red_flags": red_flags,
        "jurisdiction": jurisdiction,
        "service_name": structured["site"] or domain,
        "llm_model": "heuristics-compliance-v2-evidence",
        "structured": structured,
    }
