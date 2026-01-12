# backend/app/api/health.py
import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import PolicyAnalysis

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("")
def health(db: Session = Depends(get_db)):
    # DB ping (fast + reliable)
    db.execute("SELECT 1")

    return {
        "status": "ok",
        "db": "ok",
        "llm_enabled": os.getenv("LLM_ENABLED", "true").lower() == "true",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
    }

@router.get("/score")
def health_score(db: Session = Depends(get_db)):
    rows = db.query(PolicyAnalysis).order_by(PolicyAnalysis.created_at.desc()).limit(200).all()
    if not rows:
        return {"score": 100, "high": 0, "medium": 0, "low": 0, "total": 0}

    high = sum(1 for r in rows if r.risk_level == "HIGH")
    med = sum(1 for r in rows if r.risk_level == "MEDIUM")
    low = sum(1 for r in rows if r.risk_level == "LOW")
    total = len(rows)

    score = 100 - (high * 15 + med * 5)
    score = max(0, min(100, score))

    return {"score": score, "high": high, "medium": med, "low": low, "total": total}
