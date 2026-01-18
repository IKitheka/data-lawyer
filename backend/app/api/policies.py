from fastapi import APIRouter, Request, Depends
from app.core.auth import require_api_key
from app.core.limiter import limiter,tier_limit_provider

router = APIRouter# backend/app/api/policies.py
from fastapi import APIRouter, Request, Depends

from app.core.auth import require_api_key
from app.core.limiter import limiter, _tier_limit  # or import tier_limit_provider if you kept that name

router = APIRouter()

@router.post("/analyze")
@limiter.limit(lambda: _tier_limit)  # ✅ IMPORTANT: pass a 0-arg provider that returns a (request)->str func
async def analyze_policy(
    request: Request,
    payload: dict,
    _plan=Depends(require_api_key),
):
    # your existing logic...
    return {"ok": True}
r()

@router.post("/analyze")
@limiter.limit(tier_limit_provider)
async def analyze_policy(request: Request, payload: dict, _plan=Depends(require_api_key)):
    # your existing logic...
    return {"ok": True}
