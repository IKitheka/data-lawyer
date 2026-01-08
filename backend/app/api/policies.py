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

    latest = (
        db.query(PolicyAnalysis)
        .filter(PolicyAnalysis.domain == domain)
        .order_by(PolicyAnalysis.created_at.desc())
        .first()
    )
    policy_changed = bool(latest and latest.policy_hash != h)

    # 1) Rule-based baseline (always)
    analysis = analyze_with_rules(cleaned)

    # 2) Gemini LLM enhancement (optional)
    llm_used = False
    try:
        llm = analyze_with_gemini(cleaned, url=url, domain=domain)

        # Helpful debug: you SHOULD see this on every request when Gemini works
        print("[LLM] Gemini OK. Keys:", list(llm.keys()))

        # Override summary if Gemini provided one
        if isinstance(llm.get("summary"), str) and llm["summary"].strip():
            analysis["summary"] = llm["summary"].strip()

        # Merge red flags (combine unique)
        llm_flags = llm.get("red_flags")
        if isinstance(llm_flags, list) and llm_flags:
            llm_flags = [str(x).strip() for x in llm_flags if str(x).strip()]
            merged = list(dict.fromkeys((analysis.get("red_flags") or []) + llm_flags))
            analysis["red_flags"] = merged

        # Optionally override risk level
        rl = llm.get("risk_level")
        if isinstance(rl, str):
            rl = rl.strip().upper()
            if rl in {"LOW", "MEDIUM", "HIGH"}:
                analysis["risk_level"] = rl

        llm_used = True

    except Exception as e:
        # Don't crash the API if Gemini fails; just fall back to rules
        print("[LLM] Gemini failed, using rules only:", repr(e))

    # 3) Persist to DB
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
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # 4) Return API response
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
        # Add only if your schema supports it:
        # llm_used=llm_used,
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
