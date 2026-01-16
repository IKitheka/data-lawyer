# backend/app/core/limiter.py
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_URL = os.getenv("REDIS_URL", "").strip() or None
print("Rate limit storage:", "redis" if REDIS_URL else "memory")

if REDIS_URL:
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        r.ping()
        print("Redis ping: OK")
    except Exception as e:
        print("Redis ping: FAILED ->", repr(e))

def _rate_limit_key(request):
    user = request.headers.get("X-User-Id")
    if user:
        return f"user:{user}"

    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return f"ip:{cf_ip}"

    xff = request.headers.get("x-forwarded-for")
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"

    return get_remote_address(request)

REDIS_URL = os.getenv("REDIS_URL", "").strip() or None

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=REDIS_URL,  # ✅ this is the correct arg
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)

