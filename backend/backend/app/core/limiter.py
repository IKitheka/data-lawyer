from __future__ import annotations
import os
import time
from dataclasses import dataclass
from typing import Tuple

from limits import parse
from limits.storage import storage_from_string

# -------------------------
# Storage (Redis in prod, memory locally)
# -------------------------
def _storage_uri() -> str:
    # Use Upstash on Render:
    # Example: rediss://default:xxxxx@xxxxx.upstash.io:6379
    uri = (os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL") or "").strip()
    if uri:
        return uri

    # Local fallback
    return "memory://"

_storage = storage_from_string(_storage_uri())

# -------------------------
# Public API
# -------------------------
@dataclass
class LimitMeta:
    limit: str
    reset_seconds: int

def rate_key_from_request(api_key: str, client_id: str | None = None) -> str:
    # Prefer API key as the primary limiter key
    api_key = (api_key or "").strip()
    if api_key:
        return f"key:{api_key}"

    cid = (client_id or "").strip()
    if cid:
        return f"cid:{cid}"

    return "anon"

def check_rate_limit(key: str, limit: str) -> Tuple[bool, LimitMeta]:
    """
    limit examples:
      "30/minute"
      "120/minute"
      "600/minute"
    """
    limit = (limit or "30/minute").strip()
    item = parse(limit)

    # Use a fixed window counter (simple + stable)
    # "hit" returns True if allowed
    allowed = _storage.hit(item, key)

    # reset time: best-effort estimation using window size
    now = int(time.time())
    window = int(getattr(item, "GRANULARITY", 60))  # fallback
    # limits.parse returns RateLimitItem with per-second granularity.
    # We'll calculate reset using the expiry time we set.
    reset_seconds = 60
    try:
        reset_seconds = max(1, _storage.get_expiry(key) - now)
    except Exception:
        reset_seconds = 60

    return allowed, LimitMeta(limit=limit, reset_seconds=reset_seconds)
