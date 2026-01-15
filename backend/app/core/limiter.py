# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

def _rate_limit_key(request):
    # WARNING: X-User-Id is spoofable unless you verify it (auth).
    user = request.headers.get("X-User-Id")
    return f"user:{user}" if user else get_remote_address(request)

REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Use Redis if available, otherwise fall back to in-memory
storage_uri = REDIS_URL if REDIS_URL else "memory://"

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=storage_uri,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
