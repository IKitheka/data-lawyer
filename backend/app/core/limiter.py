# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.core.plans import resolve_plan_from_api_key, FREE

def _rate_limit_key(request):
    # 1) best: per-install client id (from extension)
    client_id = (request.headers.get("x-client-id") or "").strip()
    if client_id:
        return f"client:{client_id}"

    # 2) next best: api key (per customer/app)
    api_key = (request.headers.get("x-api-key") or "").strip()
    if api_key:
        return f"key:{api_key[:12]}"  # prefix only, don’t store full key

    # 3) last resort: ip
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return f"ip:{cf_ip}"

    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"

    return f"ip:{get_remote_address(request)}"

def tier_limit_provider(*args, **kwargs) -> str:
    request = kwargs.get("request") or (args[0] if args else None)
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
    storage_uri=REDIS_URL,  # if None -> memory (not good for multi workers)
    default_limits=[],
)
