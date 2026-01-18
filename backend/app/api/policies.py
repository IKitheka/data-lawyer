from fastapi import APIRouter, Request, Depends
from app.core.auth import require_api_key
from app.core.limiter import limiter, _tier_limit

router = APIRouter()

@router.post("/analyze")
@limiter.limit(lambda request: _tier_limit(request))
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    # your existing logic...
    return {"ok": True}
