from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

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
def health():
    # liveness: process is running
    return {"ok": True, "service": "data-lawyer-api"}


@router.get("/ready")
def readiness(db: Session = Depends(get_db)):
    """
    readiness: fail if DB is down
    Render / load balancers use this concept.
    """
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "db": "ok"}
    except SQLAlchemyError as e:
        return {"ok": False, "db": "down", "error": str(e)}


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
