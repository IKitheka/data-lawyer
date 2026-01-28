from __future__ import annotations

from fastapi import APIRouter, Request, Response, Depends, HTTPException
from backend.app.core.limiter import check_rate_limit
from backend.app.core.auth import require_api_key
from backend.app.api.schemas import AnalyzeRequest

import os
import re
from dataclasses import dataclass
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
# Contact extraction
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
    for m in re.finditer(
        r"(?i)\b(/(privacy|contact|support|dpo|data-protection|legal|preferences|opt-?out)[^\s]*)",
        raw,
    ):
        paths.append(m.group(1))
    paths = list(dict.fromkeys(paths))[:8]

    return {"emails": emails, "phones": phones, "urls": urls, "paths": paths}

# ============================================================
# Jurisdiction detection + GDPR signal
# ============================================================

def _detect_jurisdiction(text: str) -> str:
    tl = _t(text)

    # EU/GDPR signals
    if _has_any(tl, ["gdpr", "general data protection regulation", "article 6", "article 13", "article 14", "eea", "european union"]):
        return "EU"

    # US-CA signals
    if _has_any(tl, ["ccpa", "cpra", "california consumer privacy act", "do not sell", "do not share", "global privacy control", "gpc"]):
        return "US-CA"

    # UK
    if _has_any(tl, ["uk gdpr", "ico", "united kingdom", "information commissioner's office"]):
        return "UK"

    # Kenya
    if _has_any(tl, ["kenya", "data protection act 2019", "data protection act, 2019", "office of the data protection commissioner", "odpc"]):
        return "KE"

    # Others (keep for future)
    if _has_any(tl, ["popia", "south africa", "information regulator"]):
        return "ZA"
    if _has_any(tl, ["lgpd", "brazil"]):
        return "BR"
    if _has_any(tl, ["united states", "u.s.", "usa"]):
        return "US"

    return "UNKNOWN"

def _gdpr_mentioned(text: str) -> bool:
    tl = _t(text)
    return _has_any(
        tl,
        [
            "gdpr",
            "general data protection regulation",
            "article 6",
            "lawful basis",
            "controller",
            "processor",
            "data subject",
            "legitimate interests",
        ],
    )

# ============================================================
# Sentence extraction + dedupe
# ============================================================

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text or "")
    out = []

    # If a short sentence contains these signals, KEEP it (prevents missing sharing/retention lines)
    KEEP_SHORT_IF_HAS = [
        "we share", "share with", "shared with", "service provider", "service providers",
        "retain", "retention", "we retain", "we keep", "as long as necessary",
        "do not sell", "do not share", "gpc", "global privacy control",
        "opt out", "opt-out", "unsubscribe",
        "privacy@", "data protection officer", "dpo", "complaint", "supervisory authority", "odpc",
    ]

    for p in parts:
        s = _normalize_ws(p)
        if not s:
            continue

        low = s.lower()
        if low.startswith(("cookie settings", "accept cookies", "all rights reserved", "copyright")):
            continue

        # old behavior: skip very short sentences
        if len(s) < 45:
            # BUT keep it if it contains critical keywords
            if not any(k in low for k in KEEP_SHORT_IF_HAS):
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
        dup = False
        for ks in kept_sets:
            if _jaccard(ws, ks) >= sim_threshold:
                dup = True
                break
        if not dup:
            kept.append(s)
            kept_sets.append(ws)
    return kept

# ============================================================
# Evidence selection (smarter + per-section filters)
# ============================================================

_EVID_EXCLUDE_COMMON = [
    "all rights reserved",
    "cookie settings",
    "accept cookies",
]

_EVID_EXCLUDE_DATA_COLLECTED = _EVID_EXCLUDE_COMMON + [
    # keep “data collected” clean (no opt-out / marketing / retention / complaints)
    "opt out", "opt-out", "unsubscribe",
    "marketing", "advertising",
    "do not sell", "do not share", "gpc", "global privacy control",
    "retain", "retention", "as long as necessary",
    "contact", "complaint", "odpc", "supervisory authority", "ico",
]

_EVID_EXCLUDE_RETENTION = _EVID_EXCLUDE_COMMON + ["opt out", "opt-out", "unsubscribe", "contact", "complaint"]
_EVID_EXCLUDE_TRANSFERS = _EVID_EXCLUDE_COMMON + ["opt out", "opt-out", "unsubscribe", "contact", "complaint"]
_EVID_EXCLUDE_SECURITY = _EVID_EXCLUDE_COMMON + ["opt out", "opt-out", "unsubscribe", "contact", "complaint"]

# The category words requirement (so “we collect personal info” doesn’t dominate)
_DATA_CATEGORY_WORDS = [
    "identifier", "identifiers", "name", "email", "phone", "address",
    "device", "device id", "imei", "ip address", "cookie", "cookies",
    "location", "gps", "precise location",
    "payment", "billing", "card", "credit", "debit", "bank",
    "health", "medical",
    "biometric", "fingerprint", "face", "voice",
    "account", "username", "password",
    "contacts", "contact list",
    "photos", "images", "camera",
    "messages", "sms", "call logs",
    "employment", "salary",
]

def _find_evidence(
    text: str,
    include: list[str],
    exclude: list[str] | None = None,
    max_items: int = 3,
    used: set[str] | None = None,
    require_category_word: bool = False,
) -> list[str]:
    used = used or set()
    exclude = exclude or []
    sents = _dedupe_sentences(_split_sentences(text))
    out: list[str] = []

    for s in sents:
        sl = s.lower()

        if include and not any(k in sl for k in include):
            continue
        if exclude and any(bad in sl for bad in exclude):
            continue

        if require_category_word:
            if not any(cat in sl for cat in _DATA_CATEGORY_WORDS):
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
# Controller / org extraction
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
# Structured extraction (tiny + fast, rule-based)
# ============================================================

def _extract_structured(text: str, url: str, jurisdiction: str, contacts: dict, controller_hint: str | None) -> dict:
    tl = _t(text)
    domain = _domain_from_url(url)
    gdpr_signal = _gdpr_mentioned(text)

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
        "third_parties": yn(["third party", "third-party", "share with", "shared with", "disclose to"]),
        "service_providers": yn(["service provider", "vendor", "processor", "sub-processor"]),
        "sale_of_data": yn(["sell personal", "selling personal", "do not sell", "do not share"]),
    }

    retention = {
        "mentions_retention": yn(["retain", "retention", "keep for", "as long as necessary", "store for"]),
        "mentions_duration": yn(["for a period", "for x years", "for years", "for months", "until no longer"]),
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
        # “signals” not strict compliance — but useful heuristics
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
        "eu_checklist": eu,
        "usca_checklist": usca,
        "ke_checklist": ke,
        "gdpr_signal": gdpr_signal,
    }

# ============================================================
# Coverage + risk scoring
# ============================================================

def _coverage(structured: dict) -> float:
    present = 0
    total = 8

    if any(structured["rights"].values()):
        present += 1
    if structured["sharing"]["third_parties"] or structured["sharing"]["service_providers"]:
        present += 1
    if structured["retention"]["mentions_retention"]:
        present += 1
    if structured["tracking"]["cookies_or_tracking"] or structured["tracking"]["ads_marketing"]:
        present += 1
    if structured["cross_border"]["international_transfers"]:
        present += 1
    if structured["controller_hint"]:
        present += 1
    if structured["contacts"]["emails"] or structured["contacts"]["phones"] or structured["contacts"]["paths"]:
        present += 1
    if structured["jurisdiction"] != "UNKNOWN" or structured.get("gdpr_signal", False):
        present += 1

    return present / total

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
    hit(structured["retention"]["mentions_retention"] and not structured["retention"]["mentions_duration"], 6, "Retention mentioned but duration unclear.")

    hit(_has_any(tl, ["precise location", "gps", "location data"]), 10, "Collects location data (verify necessity and controls).")
    hit(_has_any(tl, ["biometric", "face recognition", "fingerprint"]), 16, "Biometric data mentioned (high sensitivity).")
    hit(_has_any(tl, ["children", "kids", "under 13", "minor"]), 10, "Children/minors mentioned (extra compliance obligations).")

    hit(_has_any(tl, ["arbitration"]), 14, "Arbitration clause may limit legal options.")
    hit(_has_any(tl, ["class action", "waive", "waiver"]), 12, "Class-action waiver may limit legal options.")
    hit(_has_any(tl, ["without notice"]) and _has_any(tl, ["change", "update"]), 10, "Policy may change without notice (weak user protections).")

    hit(structured["cross_border"]["international_transfers"], 8, "International data transfers mentioned (check safeguards).")

    j = structured["jurisdiction"]

    # Kenya
    if j == "KE":
        ke = structured["ke_checklist"]
        if not ke["purpose_mentioned"]:
            flags.append("Kenya: Missing/unclear purpose of processing.")
            score += 8
        if not ke["complaints_route"]:
            flags.append("Kenya: Missing ODPC complaint route.")
            score += 6

    # GDPR (EU or GDPR signal)
    if j == "EU" or structured.get("gdpr_signal", False):
        eu = structured["eu_checklist"]
        if not eu["lawful_basis_article6"]:
            flags.append("GDPR: Missing/unclear lawful basis (Article 6).")
            score += 8
        if not eu["dpo_or_contact"]:
            flags.append("GDPR: Missing DPO/contact details.")
            score += 6

    # US-CA
    if j == "US-CA":
        ca = structured["usca_checklist"]
        if not ca["do_not_sell_or_share"]:
            flags.append("US-CA: Missing 'Do Not Sell/Share' option.")
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
# Dynamic recommendations (risk + jurisdiction)
# Fully rule-based + specific
# ============================================================

def _dynamic_recommendations(level: str, score: int, structured: dict) -> list[str]:
    recs: list[str] = []
    j = structured["jurisdiction"]
    gdpr_signal = structured.get("gdpr_signal", False)

    if level == "HIGH":
        recs.append("Before accepting: verify you can **delete** your data and the request process actually works.")
        recs.append("Avoid providing **sensitive info** (ID numbers, precise location, biometrics) unless essential.")
    elif level == "MEDIUM":
        recs.append("Review **sharing + tracking** sections and confirm how to opt out (ads/cookies/marketing).")
    else:
        recs.append("Low risk overall — still confirm **what’s collected** and whether you can opt out of tracking/marketing.")

    if structured["tracking"]["cookies_or_tracking"] and not structured["rights"]["opt_out_marketing"]:
        recs.append("Tracking present — look for a **cookie preferences** page or consent controls (analytics/ads).")

    if structured["retention"]["mentions_retention"] and not structured["retention"]["mentions_duration"]:
        recs.append("Retention is vague — look for an actual **time period** or deletion schedule.")

    # Kenya-specific
    if j == "KE":
        ke = structured["ke_checklist"]
        recs.append("Kenya: confirm they name the **Data Controller** and reference the **Data Protection Act, 2019**.")
        if not ke["purpose_mentioned"]:
            recs.append("Kenya: missing purpose — request **purpose per data category** (why each category is collected).")
        if ke["complaints_route"]:
            recs.append("Kenya: ODPC mentioned — if unresolved, use the **ODPC complaint process** (search “ODPC Kenya lodge complaint”).")
        else:
            recs.append("Kenya: ask for the **ODPC complaint route/link** (Office of the Data Protection Commissioner).")

    # EU GDPR (or GDPR signal even if UNKNOWN)
    if j == "EU" or gdpr_signal:
        eu = structured["eu_checklist"]
        recs.append("GDPR: check the **lawful basis** per purpose (Article 6: consent/contract/legal obligation/legitimate interests).")
        if not eu["lawful_basis_article6"]:
            recs.append("GDPR: lawful basis not clear — policy should state **why processing is lawful** (Article 6).")
        if structured["cross_border"]["international_transfers"] and not eu["transfers_safeguards"]:
            recs.append("GDPR: if data leaves the EEA, look for safeguards like **SCCs** or adequacy decision.")
        if not eu["supervisory_authority"]:
            recs.append("GDPR: they should mention your right to complain to a **supervisory authority**.")

    # US-CA CCPA/CPRA
    if j == "US-CA":
        ca = structured["usca_checklist"]
        recs.append("US-CA: look for a **“Do Not Sell or Share My Personal Information”** link.")
        recs.append("US-CA: confirm they honor **Global Privacy Control (GPC)** signals.")
        if not ca["do_not_sell_or_share"]:
            recs.append("US-CA: missing Do Not Sell/Share — flag this for compliance risk.")
        if not ca["gpc_honored"]:
            recs.append("US-CA: if you use GPC, confirm they explicitly say they honor it.")
        if ca["sensitive_pi"] and not ca["limit_sensitive_use"]:
            recs.append("US-CA: sensitive PI detected — look for **“Limit the Use of Sensitive Personal Information.”**")

    # Unknown fallback
    if j == "UNKNOWN" and not gdpr_signal:
        recs.append("No clear jurisdiction detected — check for local regulator/rights/complaints channels.")
        if structured["contacts"]["emails"]:
            recs.append("Email them asking: categories collected, purposes, sharing, retention period, and deletion steps.")

    return recs[:8]

# ============================================================
# Summary builder
# ============================================================

def _mk_section(title: str, items: list[str]) -> str:
    if not items:
        return f"### {title}\n- Not clearly stated in the first 20k characters.\n"
    lines = "\n".join([f"- {x}" for x in items])
    return f"### {title}\n{lines}\n"

def _build_summary(url: str, text: str, structured: dict, score: int, level: str, red_flags: list[str]) -> str:
    used: set[str] = set()
    domain = structured["site"]

    # Data collected: must mention collection wording + category word
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
        require_category_word=True,
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

    # Sharing: allow opt-out-of-sharing lines here
    sharing = _find_evidence(
        text,
        include=[
            "we share", "share with", "sharing", "shared with", "disclose", "disclosure",
            "third party", "third-party",
            "service provider", "service providers", "vendor", "processor", "sub-processor", "affiliates",
            "partners",
            "do not sell", "do not share",
        ],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=3,
        used=used,
    )

    retention = _find_evidence(
        text,
        include=["retain", "retention", "keep for", "as long as necessary", "store for", "storage period", "we retain", "we keep", "kept for"],
        exclude=_EVID_EXCLUDE_RETENTION,
        max_items=2,
        used=used,
    )

    # Rights: opt-out belongs here (not “data collected”)
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

    security = _find_evidence(
        text,
        include=["security", "safeguard", "protect", "encryption", "secure", "measures", "access controls"],
        exclude=_EVID_EXCLUDE_SECURITY,
        max_items=2,
        used=used,
    )

    transfers = _find_evidence(
        text,
        include=["international", "transfer", "cross-border", "outside", "other countries", "eea", "stored in"],
        exclude=_EVID_EXCLUDE_TRANSFERS,
        max_items=2,
        used=used,
    )

    contact = _find_evidence(
        text,
        include=["contact", "email", "privacy@", "dpo", "data protection officer", "odpc", "complaint", "supervisory authority", "ico"],
        exclude=_EVID_EXCLUDE_COMMON,
        max_items=2,
        used=used,
    )

    cov = _coverage(structured)
    missing_topics = []
    if not collected:
        missing_topics.append("Data collected")
    if not used2:
        missing_topics.append("How data is used")
    if not sharing:
        missing_topics.append("Sharing & third parties")
    if not retention:
        missing_topics.append("Retention")
    if not rights:
        missing_topics.append("Your rights & choices")

    top_concern = "None detected"
    if red_flags:
        top_concern = red_flags[0].rstrip(".")

    recs = _dynamic_recommendations(level, score, structured)

    j = structured["jurisdiction"]
    j_display = j
    if j == "UNKNOWN" and structured.get("gdpr_signal", False):
        j_display = "UNKNOWN (GDPR mentioned)"

    lines: list[str] = []
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

    # KE checklist
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

    # EU GDPR checklist (EU or GDPR mentioned even if UNKNOWN)
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

    # US-CA checklist
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
# Endpoint
# ============================================================

@router.post("/analyze")
async def analyze_policy(request: Request, payload: dict, plan=Depends(require_api_key)):
    # ---------- Plan-based rate limiting ----------
    api_key = getattr(request.state, "api_key", None) or "anon"
    rpm = getattr(plan, "rpm", "30/minute")

    allowed, meta = check_rate_limit(api_key, rpm)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit_per_minute": meta.limit,
                "reset_seconds": meta.reset_seconds,
                "remaining": meta.remaining,
            },
        )

    # ---------- Core analysis ----------
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
        "llm_model": "heuristics-compliance-v3-fast",
        "structured": structured,
    }

