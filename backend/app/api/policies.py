# backend/app/api/policies.py
from fastapi import APIRouter, Request, Depends
from app.core.auth import require_api_key
from app.core.limiter import limiter, tier_limit_provider

router = APIRouter()

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    url = (payload.get("url") or "").strip()
    text = (payload.get("text") or "").strip()

    if not text:
        return {
            "risk_level": "UNKNOWN",
            "risk_score": None,
            "summary": "No readable policy text was detected on this page. Try scrolling, then Analyze again.",
            "red_flags": [],
            "jurisdiction": "UNKNOWN",
            "service_name": url or "UNKNOWN",
            "llm_model": "none",
        }

    # ✅ immediate response (no LLM here)
    return {
        "risk_level": "UNKNOWN",
        "risk_score": None,
        "summary": f"Received {len(text)} characters. Full analysis pending.",
        "red_flags": [],
        "jurisdiction": "UNKNOWN",
        "service_name": url or "UNKNOWN",
        "llm_model": "none",
    }
