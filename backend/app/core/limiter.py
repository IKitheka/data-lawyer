import os
from slowapi import Limiter
from slowapi.util import get_remote_address

def _rate_limit_key(request):
    # per-user/device id from extension
    client_id = request.headers.get("x-client-id")
    if client_id:
        return f"client:{client_id.strip()}"

    # fallback to proxy headers (Cloudflare/Render)
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return f"ip:{cf_ip.strip()}"

    xff = request.headers.get("x-forwarded-for")
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"

    return f"ip:{get_remote_address(request)}"

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=os.getenv("REDIS_URL") or None,
    default_limits=[os.getenv("DEFAULT_RATE_LIMIT", "60/minute")],
)
