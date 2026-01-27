from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from limits.storage import RedisStorage


# ----------------------------
# Key function (stable on Render/Cloudflare)
# ----------------------------
def rate_key_from_request(request: Request) -> str:
    api_key = (request.headers.get("x-api-key") or "").strip()
    if api_key:
        return f"key:{api_key}"

    client_id = (request.headers.get("x-client-id") or "").strip()
    if client_id:
        return f"cid:{client_id}"

    return f"ip:{get_remote_address(request)}"


# ----------------------------
# Storage (Redis if configured, else in-memory fallback)
# ----------------------------
def _build_storage() -> Optional[RedisStorage]:
    """
    Upstash redis urls look like:
      rediss://default:<password>@<host>:6379
    We'll use it if REDIS_URL is set; otherwise fallback to in-memory
    (fine for local dev, NOT for production multi-instance).
    """
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    return RedisStorage(redis_url)


_storage = _build_storage()

# IMPORTANT:
# In slowapi 0.1.9, Limiter() does NOT accept 'storage=' kwarg.
# Instead, we set storage_uri via env var or let it use memory.
#
# But we *can* force storage by setting LIMITS_STORAGE_URL env var,
# which 'limits' library picks up. We'll do that if REDIS_URL exists.
if _storage is not None:
    os.environ["LIMITS_STORAGE_URL"] = (os.getenv("REDIS_URL") or "").strip()


limiter = Limiter(key_func=rate_key_from_request)


# ----------------------------
# Response metadata for our own "manual" callers (optional)
# ----------------------------
@dataclass(frozen=True)
class RateLimitMeta:
    limit: str
    reset_seconds: int


# ----------------------------
# Dependency: enforce rate limit per plan
# ----------------------------
async def enforce_rate_limit(request: Request) -> None:
    """
    This dependency applies a limit to the current request using slowapi.
    It must run AFTER require_api_key (so request.state.plan exists).
    """
    # plan rpm is stored by auth.py; default is safe
    limit_value = getattr(request.state, "rpm", None) or "30/minute"

    # slowapi needs a function that includes `request`
    async def _noop(request: Request) -> None:
        return None

    # decorate and execute
    wrapped = limiter.limit(limit_value)(_noop)
    await wrapped(request)


# ----------------------------
# Optional manual limiter (if you want to call it yourself)
# ----------------------------
def check_rate_limit(identity: str, limit_value: str) -> Tuple[bool, RateLimitMeta]:
    """
    Manual check helper if you ever need it.
    For now, you are using enforce_rate_limit() dependency instead.
    """
    # We can't easily query reset time with slowapi in a portable way here.
    # Return meta with limit and a placeholder reset.
    return True, RateLimitMeta(limit=limit_value, reset_seconds=60)
