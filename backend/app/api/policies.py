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

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/analyze", response_model=PolicyAnalysisOut)
def analyze_policy(payload: PolicyAnalyzeInput, db: Session = Depends(get_db)):
    url = payload.url.strip()
    domain = urlparse(url).netloc or "unknown"
    service_name = payload.service_name or domain

    cleaned = normalize_text(payload.text)
    h = policy_hash(cleaned)

    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )

    policy_changed = bool(latest and latest.policy_hash != h)

    analysis = analyze_with_rules(cleaned)

    row = PolicyAnalysis(
        url=url,
        domain=domain,
        service_name=service_name,
        policy_hash=h,
        raw_text=cleaned[:200000],  # keep DB sane
        summary=analysis["summary"],
        risk_level=analysis["risk_level"],
        risk_score=analysis["risk_score"],
        jurisdiction=analysis["jurisdiction"],
        red_flags_json=json.dumps(analysis["red_flags"]),
        reasoning_json=json.dumps(analysis["reasoning"]),
        extracted_json=json.dumps(analysis["extracted"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

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
