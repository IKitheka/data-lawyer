from fastapi import APIRouter, Request, Depends
from app.core.auth import require_api_key
from app.core.limiter import limiter, tier_limit_provider

router = APIRouter()

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    # your existing logic...
    return {"ok": True, "plan":getattr(_plan, "name", None)}
