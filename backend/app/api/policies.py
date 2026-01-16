# backend/app/api/policies.py
import os
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _count_llm_calls_since(db: Session, since_dt: datetime) -> int:
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
@limiter.limit("5/minute")   # per key_func (IP or user)
@limiter.limit("1000/day")    # per key_func (IP or user)
def analyze_policy(request: Request, payload: PolicyAnalyzeInput, db: Session = Depends(get_db)):
    # DEBUG: confirm proxy headers (remove later)
    print("REMOTE:", request.client)
    print("XFF:", request.headers.get("x-forwarded-for"))
    print("CF-IP:", request.headers.get("cf-connecting-ip"))
    print("REAL-IP:", request.headers.get("x-real-ip"))

    url = (payload.url or "").strip()
    domain = urlparse(url).netloc or "unknown"
    service_name = payload.service_name or domain

    cleaned = normalize_text(payload.text or "")
    h = policy_hash(cleaned)

    # latest analysis for this domain
    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )

    # True if this domain has been seen before AND hash differs
    policy_changed = (latest is not None) and (latest.policy_hash != h)

    # 1) Rule-based baseline (always)
    analysis = analyze_with_rules(cleaned)

    # defaults (REAL values, not types)
    llm_used: bool = False
    llm_cached: bool = False
    llm: dict = {}
    llm_model: str | None = None

    # 2) Cache lookup: same domain + same hash where LLM result exists
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

    # 3) LLM lockdown gatekeeper
    llm_enabled = _env_bool("LLM_ENABLED", "true")
    cooldown_hours = _env_int("LLM_COOLDOWN_HOURS", 24)
    max_per_day = _env_int("LLM_MAX_CALLS_PER_DAY", 200)
    max_per_domain_per_day = _env_int("LLM_MAX_CALLS_PER_DOMAIN_PER_DAY", 10)

    now = _utcnow()
    since_24h = now - timedelta(hours=24)

    global_used_24h = _count_llm_calls_since(db, since_24h)
    global_quota_ok = global_used_24h < max_per_day

    domain_used_24h = _count_llm_calls_for_domain_since(db, domain, since_24h)
    domain_quota_ok = domain_used_24h < max_per_domain_per_day

    last_llm_dt = _latest_llm_call_time_for_domain(db, domain)
    cooldown_ok = True
    if last_llm_dt:
        if last_llm_dt.tzinfo is None:
            last_llm_dt = last_llm_dt.replace(tzinfo=timezone.utc)
        cooldown_ok = (now - last_llm_dt) >= timedelta(hours=cooldown_hours)

    # Spend LLM calls only if new or changed policy for this domain
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
                "global_used_24h": global_used_24h,
                "domain_used_24h": domain_used_24h,
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
                print("[LLM] Gemini returned empty (disabled/no-key).")
        else:
            if llm_cached:
                print("[LLM] Using cached Gemini output.")
            else:
                print("[LLM] Gemini blocked by gatekeeper.")
    except Exception as e:
        print("[LLM] Gemini failed, using rules only:", repr(e))
        llm = {}

    llm_available_for_response = llm_used or llm_cached
    llm_should_persist = llm_used  # only store when we actually called it this time

    # 5) Merge LLM enhancements (if any)
    if llm_available_for_response:
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

    # 6) Persist
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

        llm_summary=(llm.get("summary") if llm_should_persist else None),
        llm_red_flags_json=(json.dumps(llm.get("red_flags", [])) if llm_should_persist else None),
        llm_risk_level=(llm.get("risk_level") if llm_should_persist else None),
        llm_model=(llm_model if llm_should_persist else None),
    )

    db.add(row)
    db.commit()
    db.refresh(row)

    # 7) Response
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

    # If your schema includes these fields, set them properly
    try:
        out.llm_used = llm_used
        out.llm_model = llm_model if llm_used else None
    except Exception:
        pass

    return out
