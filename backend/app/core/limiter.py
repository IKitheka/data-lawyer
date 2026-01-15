import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from limits.storage import RedisStorage, MemoryStorage

def _rate_limit_key(request):
    # Do NOT trust X-User-Id unless you have auth.
    return get_remote_address(request)

REDIS_URL = os.getenv("REDIS_URL", "").strip()

storage = RedisStorage(REDIS_URL) if REDIS_URL else MemoryStorage()

limiter = Limiter(
    key_func=_rate_limit_key,
    storage=storage,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
