# app/api/policies.py
import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
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
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    domain = urlparse(url).netloc or "unknown"
    service_name = (payload.service_name or domain).strip() or domain

    # Normalize + protect backend from huge pages
    cleaned = normalize_text(payload.text or "")
    cleaned = cleaned[:200000]  # hard cap to protect DB + LLM

    if len(cleaned) < 200:
        # Not enough content to analyze
        return PolicyAnalysisOut(
            id=0,
            url=url,
            domain=domain,
            service_name=service_name,
            risk_level="UNKNOWN",
            risk_score=0.0,
            summary="Not enough text to analyze.",
            jurisdiction="UNKNOWN",
            red_flags=[],
            reasoning={},
            extracted={},
            policy_changed=False,
            llm_model=None,
            llm_used=None,
            model=None,
        )

    h = policy_hash(cleaned)

    # Detect change (compare with latest stored policy hash for this domain)
    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )
    policy_changed = bool(latest and latest.policy_hash != h)

    # 1) Rule-based baseline analysis
    analysis = analyze_with_rules(cleaned) or {}

    # Defaults (avoid UnboundLocalError)
    llm_used = False
    llm_cached = False
    llm = {}
    llm_model = None

    # 2) Cache lookup (domain + policy_hash + has llm fields)
    cached = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .filter(PolicyAnalysis.policy_hash == h)
        .filter(PolicyAnalysis.llm_summary.isnot(None))
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )

    if cached:
        try:
            llm = {
                "summary": cached.llm_summary,
                "red_flags": json.loads(cached.llm_red_flags_json or "[]"),
                "risk_level": cached.llm_risk_level,
                "model": cached.llm_model,
            }
            llm_cached = True
            llm_model = cached.llm_model
            print("[LLM] Cache HIT for", domain)
        except Exception as e:
            print("[LLM] Cache read failed; continuing without cache:", repr(e))
            llm = {}
            llm_cached = False
    else:
        print("[LLM] Cache MISS for", domain)

    # 3) Gemini enhancement (only when cache miss)
    try:
        if not llm_cached:
            llm = analyze_with_gemini(cleaned, url=url, domain=domain) or {}
            llm_used = True
            llm_model = llm.get("model")
            print("[LLM] Gemini OK. Keys:", list(llm.keys()))
        else:
            print("[LLM] Using cached Gemini output.")

        # Apply LLM enhancements to baseline analysis
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
        # Never crash endpoint if Gemini fails
        print("[LLM] Gemini failed, using rules only:", repr(e))

    llm_available = llm_used or llm_cached

    # 4) Persist to DB
    try:
        row = PolicyAnalysis(
            url=url,
            domain=domain,
            service_name=service_name,
            policy_hash=h,
            raw_text=cleaned,  # already capped above

            summary=str(analysis.get("summary", "") or ""),
            risk_level=str(analysis.get("risk_level", "UNKNOWN") or "UNKNOWN"),
            risk_score=float(analysis.get("risk_score", 0.0) or 0.0),
            jurisdiction=str(analysis.get("jurisdiction", "UNKNOWN") or "UNKNOWN"),

            red_flags_json=json.dumps(analysis.get("red_flags", []) or []),
            reasoning_json=json.dumps(analysis.get("reasoning", {}) or {}),
            extracted_json=json.dumps(analysis.get("extracted", {}) or {}),

            llm_summary=(llm.get("summary") if llm_available else None),
            llm_red_flags_json=(json.dumps(llm.get("red_flags", [])) if llm_available else None),
            llm_risk_level=(llm.get("risk_level") if llm_available else None),
            llm_model=(llm_model if llm_available else None),
        )

        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB write failed: {e}")

    # 5) Return response
    return PolicyAnalysisOut(
        id=row.id,
        url=row.url,
        domain=row.domain,
        service_name=row.service_name,
        risk_level=row.risk_level,
        risk_score=row.risk_score,
        summary=row.summary,
        jurisdiction=row.jurisdiction,
        red_flags=json.loads(row.red_flags_json or "[]"),
        reasoning=json.loads(row.reasoning_json or "{}"),
        extracted=json.loads(row.extracted_json or "{}"),
        policy_changed=policy_changed,
        llm_model=row.llm_model,
        llm_used=("cache" if llm_cached else ("gemini" if llm_used else None)),
        model=(llm_model if llm_available else None),
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
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    r = db.query(PolicyAnalysis).filter(PolicyAnalysis.id == analysis_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Not found")

    return {
        "id": r.id,
        "url": r.url,
        "domain": r.domain,
        "service_name": r.service_name,
        "summary": r.summary,
        "risk_level": r.risk_level,
        "risk_score": r.risk_score,
        "jurisdiction": r.jurisdiction,
        "red_flags": json.loads(r.red_flags_json or "[]"),
        "reasoning": json.loads(r.reasoning_json or "{}"),
        "extracted": json.loads(r.extracted_json or "{}"),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
