# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

def _rate_limit_key(request):
    # Prefer a user id header if you add auth later; for now fallback to IP.
    # You can also use "X-Forwarded-For" manually, but get_remote_address handles most cases.
    user = request.headers.get("X-User-Id")
    return f"user:{user}" if user else get_remote_address(request)

limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
