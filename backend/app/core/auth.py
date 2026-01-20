# backend/app/core/auth.py
from fastapi import Header, HTTPException
from app.core.plans import resolve_plan_from_api_key

def require_api_key(x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    try:
        plan = resolve_plan_from_api_key(x_api_key.strip())
        return plan
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid API key")
