"""Data Lawyer ML analyzer: clause model -> structured facts -> legal rules -> risk + evidence.

Returns the same dict shape as policies._heuristic_analyze so the extension needs no changes.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from backend.app.ml.clause_model import covered_labels, model_version, predict_clauses
from backend.app.ml.taxonomy import RIGHTS_MAP

MIN_CONF = 0.40

# Measured on 10 real privacy policies: 35%-76% of their sentences describe a data practice. Pages that are not
# policies score far lower (a legal contract 14%, two encyclopedia articles 12%-15%).
PRACTICE = {"collection", "purpose", "share_third_party", "service_provider", "sale", "advertising", "tracking",
            "retention_period", "retention_vague", "security", "right_access", "right_delete", "right_correct",
            "right_object", "right_portability", "withdraw_consent", "opt_out", "intl_transfer",
            "transfer_safeguards", "supervisory_authority", "contact", "children"}
MIN_POLICY_SHARE = 0.25
# Pages whose address looks like a privacy/terms page get a lower cutoff: terms pages carry fewer practice
# sentences (Zoom terms measured 24-29% from browser text, news pages 18-19%).
MIN_POLICY_SHARE_URL = 0.15
POLICY_SEGMENTS = {"privacy", "privacy-policy", "privacy-statement", "privacy-notice", "terms", "terms-of-service",
                   "terms-of-use", "terms-and-conditions", "legal", "cookies", "cookie-policy", "gdpr", "ccpa",
                   "cpra", "data-policy", "data-protection"}


def looks_like_policy_url(url: str) -> bool:
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    if (u.hostname or "").lower().startswith("privacy"):
        return True
    return any(seg.lower() in POLICY_SEGMENTS for seg in u.path.split("/") if seg)
MIN_SENTENCES = 10

# Search engines show snippets of the policies they link to, so a results page can look like a policy (a Google
# results page for "instagram policies" measured 28% practice sentences). The address tells us it is not one.
SEARCH_HOSTS = ("google.", "bing.com", "duckduckgo.com", "search.yahoo.com", "search.brave.com", "ecosia.org",
                "yandex.", "baidu.com")


def is_search_results_page(url: str) -> bool:
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if not any(h in host for h in SEARCH_HOSTS):
        return False
    return u.path.startswith("/search") or (u.path in ("", "/") and "q" in parse_qs(u.query))


def _evidence(clauses: List[Dict], label: str, k: int = 2) -> List[Dict[str, Any]]:
    hits = [(c["scores"].get(label, 0.0), c["clause"]) for c in clauses if label in c["labels"]]
    hits.sort(reverse=True)
    return [{"clause": t[:300], "confidence": round(s, 2)} for s, t in hits[:k]]


def _legacy_lookup(label: str, legacy: Dict[str, Any], contacts: Dict[str, List[str]], tl: str) -> bool:
    """Transitional keyword fallback for labels the model does not yet have enough data for."""
    g = lambda grp, key: bool((legacy.get(grp) or {}).get(key))  # noqa: E731
    table = {
        "right_access": g("rights", "access"), "right_delete": g("rights", "delete"),
        "right_correct": g("rights", "correct"), "right_object": g("rights", "object"),
        "right_portability": g("rights", "portability"), "withdraw_consent": g("rights", "withdraw_consent"),
        "opt_out": g("rights", "opt_out_marketing"), "service_provider": g("sharing", "service_providers"),
        "sale": g("sharing", "sale_of_data"), "intl_transfer": g("cross_border", "international_transfers"),
        "transfer_safeguards": g("cross_border", "safeguards"),
        "supervisory_authority": g("complaints", "mentions_supervisory_authority"),
        "contact": bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths")),
        "arbitration_waiver": "arbitration" in tl or "class action" in tl,
        "retention_period": g("retention", "mentions_duration"),
        "retention_vague": g("retention", "mentions_retention") and not g("retention", "mentions_duration"),
        "policy_change_no_notice": "without notice" in tl and ("change" in tl or "update" in tl),
    }
    return bool(table.get(label, False))


def ml_analyze(url: str, text: str, domain: str, jurisdiction: str, contacts: Dict[str, List[str]],
               legacy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    clauses = predict_clauses(text)
    covered = covered_labels()
    practice_n = sum(1 for c in clauses if PRACTICE & set(c["labels"]))
    policy_share = practice_n / max(1, len(clauses))
    search_page = is_search_results_page(url)
    min_share = MIN_POLICY_SHARE_URL if looks_like_policy_url(url) else MIN_POLICY_SHARE
    looks_like_policy = len(clauses) >= MIN_SENTENCES and policy_share >= min_share and not search_page
    tl = (text or "").lower()
    legacy = legacy or {}
    fallback_used: set = set()

    def has(l: str) -> bool:
        if l in covered:
            hit = any(l in c["labels"] for c in clauses)
            if l == "contact":  # an email/phone/contact page in the text is a fact, not a classifier guess
                hit = hit or bool(contacts.get("emails") or contacts.get("phones") or contacts.get("paths"))
            return hit
        fallback_used.add(l)
        return _legacy_lookup(l, legacy, contacts, tl)

    ev = {l: _evidence(clauses, l) for l in set(sum([c["labels"] for c in clauses], []))}

    rights = {k: has(v) for k, v in RIGHTS_MAP.items()}
    sharing = {
        "third_parties": has("share_third_party"),
        "service_providers": has("service_provider"),
        "sale_of_data": has("sale"),
    }
    retention = {
        "mentions_retention": has("retention_period") or has("retention_vague"),
        "mentions_duration": has("retention_period"),
    }
    tracking = {"cookies_or_tracking": has("tracking"), "ads_marketing": has("advertising")}
    cross_border = {"international_transfers": has("intl_transfer"), "safeguards": has("transfer_safeguards")}
    complaints = {
        "mentions_supervisory_authority": has("supervisory_authority"),
        "mentions_odpc": jurisdiction == "KE" and has("supervisory_authority"),
        "mentions_ico": jurisdiction == "UK" and has("supervisory_authority"),
    }

    # ---- Legal rule engine (deterministic; weights mirror the old heuristic for continuity) ----
    score = 10
    red_flags: List[str] = []

    def add(cond: bool, pts: int, msg: str, label: str | None = None):
        nonlocal score
        if cond:
            score += pts
            quote = ev.get(label, [{}])[0].get("clause") if label else None
            red_flags.append(f"{msg} Evidence: \"{quote}\"" if quote else msg)

    add(sharing["third_parties"], 12, "Shares data with third parties (check scope & opt-out).", "share_third_party")
    add(sharing["sale_of_data"], 18, "Mentions selling/sharing personal info (verify opt-out).", "sale")
    add(tracking["ads_marketing"], 8, "Data used for advertising/marketing (look for opt-out).", "advertising")
    add(tracking["cookies_or_tracking"], 6, "Tracking/cookies/analytics mentioned (check consent controls).", "tracking")
    vague, period = has("retention_vague"), has("retention_period")
    add(vague and not period, 6, "Retention mentioned but duration unclear.", "retention_vague")
    add(vague and period, 3, "Some retention statements are open-ended (no clear period or end condition).", "retention_vague")
    add(has("arbitration_waiver"), 14, "Arbitration/class-action waiver may limit legal options.", "arbitration_waiver")
    add(has("policy_change_no_notice"), 10, "Policy may change without notice.", "policy_change_no_notice")
    add(cross_border["international_transfers"] and not cross_border["safeguards"], 8,
        "International transfers without stated safeguards.", "intl_transfer")
    regulated = jurisdiction in ("EU", "UK", "KE", "US-CA")
    add(regulated and not any(rights.values()), 8, f"No user rights described although {jurisdiction} law applies.")
    add(regulated and not (complaints["mentions_supervisory_authority"] or has("contact")), 4,
        "No complaint route or contact point found.")

    score = max(0, min(100, score))
    level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"

    missing: List[str] = []
    if not has("collection"):
        missing.append("Data collected")
    if not has("purpose"):
        missing.append("How data is used")
    if not (sharing["third_parties"] or sharing["service_providers"]):
        missing.append("Sharing & third parties")
    if not retention["mentions_retention"]:
        missing.append("Retention")
    if not any(rights.values()):
        missing.append("Your rights & choices")

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

    version = model_version()
    structured = {
        "site": domain,
        "jurisdiction": jurisdiction,
        "controller_hint": domain,
        "contacts": contacts,
        "rights": rights,
        "sharing": sharing,
        "retention": retention,
        "tracking": tracking,
        "cross_border": cross_border,
        "complaints": complaints,
        "gdpr_signal": jurisdiction == "EU",
        "llm_used": False,
        "ml_used": True,
        "clauses_analyzed": len(clauses),
        "looks_like_policy": looks_like_policy,
        "policy_share": round(policy_share, 3),
        "model_labels": sorted(covered),
        "legacy_fallback_labels": sorted(fallback_used),
        "evidence": ev,
    }
    if not looks_like_policy:
        # Not a privacy policy: any "finding" would be noise, so say so instead of showing a risk score.
        for grp in ("rights", "sharing", "retention", "tracking", "cross_border", "complaints"):
            structured[grp] = {k: False for k in structured[grp]}
        structured["evidence"] = {}
        level, score, red_flags = "LOW", 0, []
        summary = "\n".join([
            "## Policy Summary",
            f"**Site:** {domain}",
            "",
            "### This page doesn't look like a privacy policy",
            ("- This is a search results page, not a privacy policy." if search_page else
             f"- Only {policy_share:.0%} of its sentences describe how personal data is handled "
             f"(real policies score {min_share:.0%} or more)."),
            "- No risk assessment was made, so the score of 0 means \"not assessed\", not \"safe\".",
            "- Open the privacy policy page itself and run the analysis there.",
        ])
    return {
        "risk_level": level,
        "risk_score": score,
        "summary": summary,
        "red_flags": red_flags[:12],
        "jurisdiction": jurisdiction,
        "service_name": domain,
        "llm_model": f"datalawyer-{version}",  # key name kept so the extension keeps working
        "structured": structured,
    }
