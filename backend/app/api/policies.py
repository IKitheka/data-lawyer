# backend/app/api/policies.py
from fastapi import APIRouter, Depends, Request
from app.core.auth import require_api_key
from app.core.limiter import limiter, _tier_limit  # import tier limit function

router = APIRouter()

@router.post("/analyze")
@limiter.limit(_tier_limit)  # ✅ dynamic per-tier daily limit
async def analyze_policy(request: Request, plan = Depends(require_api_key)):
    # plan.name will be "free" / "pro" / "business"
    payload = await request.json()

    # ... your existing analysis logic ...
    # return your normal JSON response
    return {
        "ok": True,
        "plan": plan.name,
        "summary": "example",
        "risk_level": "LOW",
        "risk_score": 12,
        "red_flags": [],
    }
