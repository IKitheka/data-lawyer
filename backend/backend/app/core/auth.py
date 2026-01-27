from __future__ import annotations

from fastapi import Header, HTTPException, Request
from backend.app.core.plans import resolve_plan_from_api_key, Plan


def require_api_key(
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> Plan:
    api_key = (x_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    try:
        plan = resolve_plan_from_api_key(api_key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Make available to handlers + limiter dependency
    request.state.api_key = api_key
    request.state.plan = plan.name
    request.state.rpm = plan.rpm
    return plan
