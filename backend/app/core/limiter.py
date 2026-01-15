# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

def _rate_limit_key(request):
    # If you later add auth, set X-User-Id from your extension
    user = request.headers.get("X-User-Id")
    if user:
        return f"user:{user}"
    # Fall back to Cloudflare / proxy IP if present
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return f"ip:{cf_ip}"
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"
    return get_remote_address(request)

limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
