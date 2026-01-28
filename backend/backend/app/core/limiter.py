from __future__ import annotations

import os
import time
from dataclasses import dataclass

import redis

@dataclass(frozen=True)
class RateMeta:
    limit: int
    reset_seconds: int
    remaining: int

def _parse_rpm(rpm: str) -> int:
    """
    Accepts:
      "30/minute"
      "120/min"
      "600/m"
    Returns integer requests per minute.
    """
    s = (rpm or "").strip().lower()
    if not s:
        return 30
    # pick the first integer found
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            break
    return int(num) if num else 30

def _redis_client() -> redis.Redis:
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        raise RuntimeError("REDIS_URL is not set")
    # Upstash uses rediss://
    return redis.from_url(url, decode_responses=True)

def check_rate_limit(identity: str, rpm: str) -> tuple[bool, RateMeta]:
    """
    Sliding-ish window per minute (fixed window):
    - Key: rl:{identity}:{YYYYMMDDHHMM}
    - INCR
    - EXPIRE 70 seconds
    """
    limit = _parse_rpm(rpm)
    now = int(time.time())
    window = now // 60  # minute bucket
    key = f"rl:{identity}:{window}"

    r = _redis_client()

    count = r.incr(key)
    if count == 1:
        # keep a little buffer past 60s
        r.expire(key, 70)

    # seconds until next minute bucket
    reset_seconds = 60 - (now % 60)
    remaining = max(0, limit - count)

    allowed = count <= limit
    return allowed, RateMeta(limit=limit, reset_seconds=reset_seconds, remaining=remaining)
