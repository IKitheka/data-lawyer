from fastapi import Header, HTTPException
from backend.app.core.plans import resolve_plan_from_api_key

def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    api_key = (x_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    # MVP: accept any non-empty key
    return resolve_plan_from_api_key(api_key)
