# backend/app/core/limiter.py
import os
from slowapi import Limiter

def _client_ip(request):
    # Cloudflare real client IP header
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()

    # Standard proxy chain header: first IP is the original client
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()

    # Fallback
    if request.client:
        return request.client.host

    return "unknown"

def _rate_limit_key(request):
    # "Per-user" only works if you have REAL auth.
    # For now, you can rate limit per API key if you add one, else per IP.
    user = request.headers.get("X-User-Id")
    return f"user:{user}" if user else f"ip:{_client_ip(request)}"

REDIS_URL = os.getenv("REDIS_URL", "").strip()
storage_uri = REDIS_URL if REDIS_URL else "memory://"

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=storage_uri,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
