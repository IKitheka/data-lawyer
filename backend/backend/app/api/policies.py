from __future__ import annotations

import os
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.core.auth import require_api_key
from backend.app.core.analytics import log_event
from backend.app.core.limiter import check_rate_limit
from backend.app.api.schemas import AnalyzeRequest

# Optional LLM upgrade (only used if DL_USE_LLM=1 and OPENAI_API_KEY is set)
try:
    from backend.app.core.llm_judge import llm_analyze_policy
except Exception:
    llm_analyze_policy = None  # type: ignore


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


def _t(s: str) -> str:
    return (s or "").lower()


def _has_any(text_lower: str, phrases: List[str]) -> bool:
    return any(p in text_lower for p in phrases)


# ============================================================
# Contact extraction (lightweight)
# ============================================================

_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")


def _extract_contacts(text: str) -> Dict[str, List[str]]:
    raw = text or ""

    emails = list(dict.fromkeys([e.lower() for e in _EMAIL_RE.findall(raw)]))[:5]

    phones: List[str] = []
    for p in _PHONE_RE.findall(raw):
        p2 = _normalize_ws(p)
        if len(re.sub(r"\D", "", p2)) >= 9:
            phones.append(p2)
    phones = list(dict.fromkeys(phones))[:4]

    paths: List[str] = []
    for m in re.finditer(
        r"(?i)\b(/(privacy|contact|support|dpo|data-protection|legal|preferences|opt-?out)[^\s]*)",
        raw,
    ):
        paths.append(m.group(1))
    paths = list(dict.fromkeys(paths))[:8]

    return {"emails": emails, "phones": phones, "paths": paths}


# ============================================================
# Jurisdiction detection (signals only)
# ============================================================

def _detect_jurisdiction(text: str) -> str:
    tl = _t(text)

    if _has_any(tl, ["gdpr", "general data protection regulation", "eea", "european union", "article 6", "article 13", "article 14"]):
        return "EU"

    if _has_any(tl, ["ccpa", "cpra", "california consumer privacy act", "do not sell", "do not share", "global privacy control", "gpc"]):
        return "US-CA"

    if _has_any(tl, ["uk gdpr", "ico", "information commissioner's office", "united kingdom"]):
        return "UK"

    if _has_any(tl, ["kenya", "data protection act 2019", "office of the data protection commissioner", "odpc"]):
        return "KE"

    if _has_any(tl, ["popia", "south africa", "information regulator"]):
        return "ZA"

    if _has_any(tl, ["lgpd", "brazil"]):
        return "BR"

    if _has_any(tl, ["united states", "u.s.", "usa"]):
        return "US"

    return "UNKNOWN"


# ============================================================
# Heuristic analyzer (fallback if LLM disabled/unavailable)
# ============================================================

def _heuristic_analyze(url: str, text: str) -> Dict[str, Any]:
    """
    Fast + deterministic fallback.
    This keeps your app working even if LLM is off or fails.
    """
    domain = _domain_from_url(url)
    tl = _t(text)

    jurisdiction = _detect_jurisdiction(text)
    contacts = _extract_contacts(text)

    # Simple topic detection (signals)
    rights = {
        "access": _has_any(tl, ["right of access", "access request", "data subject access"]),
        "delete": _has_any(tl, ["delete", "erasure", "right to be forgotten", "deletion request"]),
        "correct": _has_any(tl, ["correct", "rectify", "rectification", "correction"]),
        "object": _has_any(tl, ["right to object", "object to processing"]),
        "portability": _has_any(tl, ["data portability"]),
        "withdraw_consent": _has_any(tl, ["withdraw consent"]),
        "opt_out_marketing": _has_any(tl, ["opt out", "opt-out", "unsubscribe", "marketing"]),
    }

    sharing = {
        "third_parties": _has_any(tl, ["third party", "third-party", "share with", "shared with", "disclose to", "partners"]),
        "service_providers": _has_any(tl, ["service provider", "vendor", "processor", "sub-processor"]),
        "sale_of_data": _has_any(tl, ["sell personal", "selling personal", "do not sell", "do not share"]),
    }

    retention = {
        "mentions_retention": _has_any(tl, ["retain", "retention", "as long as necessary", "store for", "keep for"]),
        "mentions_duration": _has_any(tl, ["for a period", "for years", "for months", "x years", "until no longer"]),
    }

    tracking = {
        "cookies_or_tracking": _has_any(tl, ["cookie", "tracking", "analytics", "pixel", "sdk"]),
        "ads_marketing": _has_any(tl, ["advertising", "targeted ads", "personalized ads", "interest-based", "marketing"]),
    }

    cross_border = {
        "international_transfers": _has_any(tl, ["international transfer", "cross-border", "outside your country", "other countries", "stored in"]),
        "safeguards": _has_any(tl, ["standard contractual clauses", "scc", "adequacy", "safeguards"]),
    }

    complaints = {
        "mentions_supervisory_authority": _has_any(tl, ["supervisory authority", "lodge a complaint"]),
        "mentions_odpc": _has_any(tl, ["odpc", "office of the data protection commissioner"]),
        "mentions_ico": _has_any(tl, ["ico", "information commissioner's office"]),
    }

    # Risk scoring (simple, explainable)
    score = 10
    red_flags: List[str] = []

    def add(cond: bool, points: int, msg: str):
        nonlocal score
        if cond:
            score += points
            red_flags.append(msg)

    add(sharing["third_parties"], 12, "Shares data with third parties (check scope & opt-out).")
    add(sharing["sale_of_data"], 18, "Mentions selling/sharing personal info (verify opt-out).")
    add(tracking["ads_marketing"], 8, "Data used for advertising/marketing (look for opt-out).")
    add(tracking["cookies_or_tracking"], 6, "Tracking/cookies/analytics mentioned (check consent controls).")
    add(retention["mentions_retention"] and not retention["mentions_duration"], 6, "Retention mentioned but duration unclear.")
    add(_has_any(tl, ["arbitration"]), 14, "Arbitration clause may limit legal options.")
    add(_has_any(tl, ["class action", "waive", "waiver"]), 12, "Class-action waiver may limit legal options.")
    add(_has_any(tl, ["without notice"]) and _has_any(tl, ["change", "update"]), 10, "Policy may change without notice.")

    score = max(0, min(100, score))

    if score >= 70:
        level = "HIGH"
    elif score >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    # Missing topics summary (basic)
    missing: List[str] = []
    if not _has_any(tl, ["we collect", "information we collect", "personal information we collect", "categories of personal information"]):
        missing.append("Data collected")
    if not _has_any(tl, ["we use", "how we use", "used to", "purpose of processing"]):
        missing.append("How data is used")
    if not (sharing["third_parties"] or sharing["service_providers"]):
        missing.append("Sharing & third parties")
    if not retention["mentions_retention"]:
        missing.append("Retention")
    if not any(rights.values()):
        missing.append("Your rights & choices")

    # Markdown summary (what your extension renders)
    summary = "\n".join([
        "## Policy Summary",
        f"**Site:** {domain}",
        "",
        "### Key takeaways",
        f"- Risk level: **{level}** (score **{score}/100**)",
        f"- Jurisdiction signals: **{jurisdiction}**",
        f"- Missing/unclear: **{', '.join(missing) if missing else 'None detected'}**",
        f"- Top concern: {red_flags[0] if red_flags else 'None detected'}",
        "",
        "### What to do next",
        "- Confirm what data is collected, how it’s used, and whether you can opt out of tracking/marketing.",
        "- Look for a retention period and deletion steps.",
        "",
    ]).strip()

    structured = {
        "site": domain,
        "jurisdiction": jurisdiction,
        "controller_hint": domain,
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
        "gdpr_signal": jurisdiction == "EU" or _has_any(tl, ["gdpr", "general data protection regulation"]),
        "llm_used": False,
    }

    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,
        "red_flags": red_flags[:12],
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": "heuristics-v1",
        "structured": structured,
    }


# ============================================================
# Main endpoint
# ============================================================

@router.post("/analyze")
async def analyze_policy(
    request: Request,
    body: AnalyzeRequest,
    plan=Depends(require_api_key),
):
    # ---------------------------
    # Rate limiting (plan-based)
    # ---------------------------
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

    # ---------------------------
    # Input
    # ---------------------------
    url = str(body.url or "").strip()
    text = str(body.text or "").strip()
    service_name = str(getattr(body, "service_name", "") or "").strip() or _domain_from_url(url)

    if not url or not text:
        raise HTTPException(status_code=400, detail={"error": "Missing url/text"})

    text = text[:20000]  # safety limit

    # ---------------------------
    # Choose analyzer (LLM vs heuristic)
    # ---------------------------
    use_llm = os.getenv("DL_USE_LLM", "0") == "1"
    result: Dict[str, Any]

    if use_llm and llm_analyze_policy is not None:
        try:
            llm = await llm_analyze_policy(url=url, text=text)

            # Normalize + enforce safe shape
            risk_score = int(llm.get("risk_score", 0))
            risk_score = max(0, min(100, risk_score))

            risk_level = str(llm.get("risk_level", "LOW")).upper()
            if risk_level not in ("LOW", "MEDIUM", "HIGH"):
                risk_level = "LOW" if risk_score < 40 else ("MEDIUM" if risk_score < 70 else "HIGH")

            red_flags = llm.get("red_flags", []) or []
            if not isinstance(red_flags, list):
                red_flags = []
            red_flags = [str(x) for x in red_flags][:12]

            jurisdiction = str(llm.get("jurisdiction", "UNKNOWN")).upper()
            summary_md = str(llm.get("summary_markdown", "") or "").strip()

            # Ensure structured exists
            structured = {
                "site": _domain_from_url(url),
                "jurisdiction": jurisdiction,
                "controller_hint": llm.get("controller_hint"),
                "contacts": llm.get("contacts") or _extract_contacts(text),
                "rights": llm.get("rights") or {},
                "sharing": llm.get("sharing") or {},
                "retention": llm.get("retention") or {},
                "tracking": llm.get("tracking") or {},
                "cross_border": llm.get("cross_border") or {},
                "complaints": llm.get("complaints") or {},
                "gdpr_signal": bool(llm.get("jurisdiction") == "EU") or "gdpr" in _t(text),
                "llm_used": True,
            }

            result = {
                "risk_level": risk_level,
                "risk_score": risk_score,
                "summary": summary_md or _heuristic_analyze(url, text)["summary"],
                "red_flags": red_flags,
                "jurisdiction": jurisdiction,
                "service_name": service_name,
                "llm_model": os.getenv("DL_LLM_MODEL", "gpt-4o-mini"),
                "structured": structured,
            }
        except Exception:
            # If LLM fails, fall back to heuristic (never break prod)
            result = _heuristic_analyze(url, text)
            result["service_name"] = service_name
    else:
        result = _heuristic_analyze(url, text)
        result["service_name"] = service_name

    # ---------------------------
    # Analytics
    # ---------------------------
    try:
        log_event(
            "policy_analyzed",
            {
                "site": result.get("structured", {}).get("site"),
                "jurisdiction": result.get("jurisdiction"),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
                "client_id": request.headers.get("X-Client-Id"),
                "llm_used": bool(result.get("structured", {}).get("llm_used")),
                "env": os.getenv("ENV", "production"),
            },
        )
    except Exception:
        pass

    # ---------------------------
    # Response (extension expects these keys)
    # ---------------------------
    return {
        "risk_level": result["risk_level"],
        "risk_score": result["risk_score"],
        "summary": result["summary"],
        "red_flags": result["red_flags"],
        "jurisdiction": result["jurisdiction"],
        "service_name": result["service_name"],
        "llm_model": result.get("llm_model", "heuristics-v1"),
        "structured": result["structured"],
    }
