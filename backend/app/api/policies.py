# backend/app/api/policies.py
import os
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import PolicyAnalysis
from app.schemas.policy import PolicyAnalyzeInput, PolicyAnalysisOut
from app.ai.text_cleaner import normalize_text
from app.ai.hash_utils import policy_hash
from app.ai.policy_rules import analyze_with_rules
from app.ai.gemini import analyze_with_gemini
from app.core.limiter import limiter

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower().strip() == "true"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _utcnow():
    return datetime.now(timezone.utc)


def _count_llm_calls_since(db: Session, since_dt: datetime) -> int:
    # Count rows where we actually stored an LLM summary (means LLM ran successfully)
    return (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.created_at >= since_dt)
        .filter(PolicyAnalysis.llm_summary.isnot(None))
        .count()
    )


def _count_llm_calls_for_domain_since(db: Session, domain: str, since_dt: datetime) -> int:
    return (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .filter(PolicyAnalysis.created_at >= since_dt)
        .filter(PolicyAnalysis.llm_summary.isnot(None))
        .count()
    )


def _latest_llm_call_time_for_domain(db: Session, domain: str):
    r = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .filter(PolicyAnalysis.llm_summary.isnot(None))
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )
    return r.created_at if r else None


@router.post("/analyze", response_model=PolicyAnalysisOut)
@limiter.limit("10/minute")          # per IP
@limiter.limit("200/day")            # per IP daily cap
def analyze_policy(payload: PolicyAnalyzeInput, db: Session = Depends(get_db)):
    url = (payload.url or "").strip()
    domain = urlparse(url).netloc or "unknown"
    service_name = payload.service_name or domain

    cleaned = normalize_text(payload.text or "")
    h = policy_hash(cleaned)

    # latest analysis for this domain (for change detection)
    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )
    policy_changed = bool(latest and latest.policy_hash != h)

    # 1) Rule-based baseline (always)
    analysis = analyze_with_rules(cleaned)

    # ---- defaults ----
    llm_used = False
    llm_cached = False
    llm = {}
    llm_model = None

    # 2) LLM cache lookup: domain + policy_hash
    cached = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .filter(PolicyAnalysis.policy_hash == h)
        .filter(PolicyAnalysis.llm_summary.isnot(None))
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )

    if cached:
        llm = {
            "summary": cached.llm_summary,
            "red_flags": json.loads(cached.llm_red_flags_json or "[]"),
            "risk_level": cached.llm_risk_level,
            "model": cached.llm_model,
        }
        llm_cached = True
        llm_model = cached.llm_model
        print("[LLM] Cache HIT for", domain)
    else:
        print("[LLM] Cache MISS for", domain)

    # 3) LLM LOCKDOWN GATEKEEPER
    llm_enabled = _env_bool("LLM_ENABLED", "true")

    cooldown_hours = _env_int("LLM_COOLDOWN_HOURS", 24)
    max_per_day = _env_int("LLM_MAX_CALLS_PER_DAY", 200)
    max_per_domain_per_day = _env_int("LLM_MAX_CALLS_PER_DOMAIN_PER_DAY", 10)

    now = _utcnow()
    since_24h = now - timedelta(hours=24)

    # ✅ Global quota
    global_used_24h = _count_llm_calls_since(db, since_24h)
    global_quota_ok = global_used_24h < max_per_day

    # ✅ Domain quota
    domain_used_24h = _count_llm_calls_for_domain_since(db, domain, since_24h)
    domain_quota_ok = domain_used_24h < max_per_domain_per_day

    # ✅ Cooldown
    last_llm_dt = _latest_llm_call_time_for_domain(db, domain)
    cooldown_ok = True
    if last_llm_dt:
        # created_at might be naive depending on your DB model; assume UTC if naive
        if last_llm_dt.tzinfo is None:
            last_llm_dt = last_llm_dt.replace(tzinfo=timezone.utc)
        cooldown_ok = (now - last_llm_dt) >= timedelta(hours=cooldown_hours)

    # ✅ Policy-change rule (only spend money if policy is new/changed or never seen)
    # If you have a cached LLM result for same hash, never call LLM.
    # If policy isn't changed, we generally do NOT call LLM (saves cost).
    policy_new_or_changed = (latest is None) or policy_changed

    should_call_llm = (
        llm_enabled
        and (not llm_cached)
        and policy_new_or_changed
        and global_quota_ok
        and domain_quota_ok
        and cooldown_ok
    )

    if not llm_cached:
        print(
            "[LLM] Gate:",
            {
                "enabled": llm_enabled,
                "policy_new_or_changed": policy_new_or_changed,
                "global_quota_ok": global_quota_ok,
                "domain_quota_ok": domain_quota_ok,
                "cooldown_ok": cooldown_ok,
                "will_call": should_call_llm,
            },
        )

    # 4) Gemini call (ONLY if allowed)
    try:
        if should_call_llm:
            llm = analyze_with_gemini(cleaned, url=url, domain=domain) or {}
            if llm:
                llm_used = True
                llm_model = llm.get("model")
                print("[LLM] Gemini OK. Keys:", list(llm.keys()))
            else:
                print("[LLM] Gemini skipped/disabled/no-key.")
        else:
            if llm_cached:
                print("[LLM] Using cached Gemini output.")
            else:
                print("[LLM] Gemini blocked by gatekeeper.")
    except Exception as e:
        print("[LLM] Gemini failed, using rules only:", repr(e))
        llm = {}

    llm_available = llm_used or llm_cached

    # 5) Merge LLM enhancements (if any)
    if llm_available:
        if isinstance(llm.get("summary"), str) and llm["summary"].strip():
            analysis["summary"] = llm["summary"].strip()

        llm_flags = llm.get("red_flags")
        if isinstance(llm_flags, list) and llm_flags:
            llm_flags = [str(x).strip() for x in llm_flags if str(x).strip()]
            merged = list(dict.fromkeys((analysis.get("red_flags") or []) + llm_flags))
            analysis["red_flags"] = merged

        rl = llm.get("risk_level")
        if isinstance(rl, str):
            rl = rl.strip().upper()
            if rl in {"LOW", "MEDIUM", "HIGH"}:
                analysis["risk_level"] = rl

    # 6) Persist to DB
    row = PolicyAnalysis(
        url=url,
        domain=domain,
        service_name=service_name,
        policy_hash=h,
        raw_text=cleaned[:200000],

        summary=analysis.get("summary", ""),
        risk_level=analysis.get("risk_level", "UNKNOWN"),
        risk_score=float(analysis.get("risk_score", 0.0)),
        jurisdiction=analysis.get("jurisdiction", "UNKNOWN"),

        red_flags_json=json.dumps(analysis.get("red_flags", [])),
        reasoning_json=json.dumps(analysis.get("reasoning", {})),
        extracted_json=json.dumps(analysis.get("extracted", {})),

        llm_summary=(llm.get("summary") if llm_available else None),
        llm_red_flags_json=(json.dumps(llm.get("red_flags", [])) if llm_available else None),
        llm_risk_level=(llm.get("risk_level") if llm_available else None),
        llm_model=(llm_model if llm_available else None),
    )

    db.add(row)
    db.commit()
    db.refresh(row)

    # 7) Return
    out = PolicyAnalysisOut(
        id=row.id,
        url=row.url,
        domain=row.domain,
        service_name=row.service_name,
        risk_level=row.risk_level,
        risk_score=row.risk_score,
        summary=row.summary,
        jurisdiction=row.jurisdiction,
        red_flags=json.loads(row.red_flags_json),
        reasoning=json.loads(row.reasoning_json),
        extracted=json.loads(row.extracted_json),
        policy_changed=policy_changed,
    )

    # optional fields (if your schema includes them)
    try:
        out.llm_used = llm_model if llm_available else None
        out.llm_model = llm_model if llm_available else None
    except Exception:
        pass

    return out
