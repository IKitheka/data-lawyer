from fastapi import Header, HTTPException
from backend.app.core.plans import resolve_plan_from_api_key

def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """
    MVP auth:
    - If header missing/empty -> 401
    - Otherwise -> accept and return a plan (currently FREE)
    """
    api_key = (x_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    # For now, accept any key (plans.py always returns FREE anyway)
    return resolve_plan_from_api_key(api_key)
