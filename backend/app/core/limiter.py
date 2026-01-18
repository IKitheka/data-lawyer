# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.core.plans import resolve_plan_from_api_key, FREE

def _rate_limit_key(request):
    client_id = request.headers.get("x-client-id")
    if client_id:
        return f"client:{client_id}"

    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return f"ip:{cf_ip}"

    xff = request.headers.get("x-forwarded-for")
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"

    return f"ip:{get_remote_address(request)}"

def tier_limit_provider(*args, **kwargs) -> str:
    """
    SlowAPI sometimes calls the provider with request, sometimes differently.
    This wrapper handles both safely.
    """
    request = kwargs.get("request")
    if request is None and args:
        request = args[0]

    # No request? fall back to FREE
    if request is None:
        return FREE.daily_limit

    api_key = (request.headers.get("x-api-key") or "").strip()
    try:
        plan = resolve_plan_from_api_key(api_key)
    except Exception:
        plan = FREE

    return plan.daily_limit

REDIS_URL = os.getenv("REDIS_URL", "").strip() or None

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=REDIS_URL,  # Redis if set, memory if None
    default_limits=[],
)
