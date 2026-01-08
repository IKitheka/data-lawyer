import json
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

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/analyze", response_model=PolicyAnalysisOut)
def analyze_policy(payload: PolicyAnalyzeInput, db: Session = Depends(get_db)):
    url = (payload.url or "").strip()
    domain = urlparse(url).netloc or "unknown"
    service_name = payload.service_name or domain

    cleaned = normalize_text(payload.text or "")
    h = policy_hash(cleaned)

    # last analysis for this domain (to detect change)
    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )
    policy_changed = bool(latest and latest.policy_hash != h)

    # 1) Rule-based baseline
    analysis = analyze_with_rules(cleaned)

    # ---- defaults to avoid UnboundLocalError ----
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

    # 3) Gemini LLM enhancement (only on cache miss)
    try:
        if not llm_cached:
            llm = analyze_with_gemini(cleaned, url=url, domain=domain)
            llm_used = True
            llm_model = llm.get("model")
            print("[LLM] Gemini OK. Keys:", list(llm.keys()))
        else:
            print("[LLM] Using cached Gemini output.")

        # Apply LLM enhancements (if any) to the response analysis
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

    except Exception as e:
        print("[LLM] Gemini failed, using rules only:", repr(e))

    llm_available = llm_used or llm_cached

    # 4) Persist to DB
    row = PolicyAnalysis(
        url=url,
        domain=domain,
        service_name=service_name,
        policy_hash=h,
        raw_text=cleaned[:200000],  # keep DB sane

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

    # 5) Return API response
    return PolicyAnalysisOut(
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


@router.get("/latest")
def latest_analyses(db: Session = Depends(get_db)):
    rows = db.query(PolicyAnalysis).order_by(PolicyAnalysis.created_at.desc()).limit(50).all()
    return [
        {
            "id": r.id,
            "domain": r.domain,
            "service_name": r.service_name,
            "risk_level": r.risk_level,
            "risk_score": r.risk_score,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    r = db.query(PolicyAnalysis).filter(PolicyAnalysis.id == analysis_id).first()
    if not r:
        return {"error": "Not found"}

    return {
        "id": r.id,
        "url": r.url,
        "domain": r.domain,
        "service_name": r.service_name,
        "summary": r.summary,
        "risk_level": r.risk_level,
        "risk_score": r.risk_score,
        "jurisdiction": r.jurisdiction,
        "red_flags": json.loads(r.red_flags_json),
        "reasoning": json.loads(r.reasoning_json),
        "extracted": json.loads(r.extracted_json),
        "created_at": r.created_at.isoformat(),
    }
