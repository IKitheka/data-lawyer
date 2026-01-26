import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Plan:
    name: str

FREE = Plan("free")
PRO = Plan("pro")
BUSINESS = Plan("business")

def _norm(s: str | None) -> str:
    return (s or "").strip()

def _configured_keys() -> dict[str, str]:
    """
    Reads API keys from environment.
    If none are set, dict will be empty.
    """
    keys = {
        "master": _norm(os.getenv("DL_API_KEY")),
        "free": _norm(os.getenv("DL_API_KEY_FREE")),
        "pro": _norm(os.getenv("DL_API_KEY_PRO")),
        "business": _norm(os.getenv("DL_API_KEY_BUSINESS")),
    }
    return {k: v for k, v in keys.items() if v}

def resolve_plan_from_api_key(api_key: str) -> Plan:
    """
    Behavior:
    - If NO DL_API_KEY* env vars are configured => accept any non-empty key (local-dev friendly).
    - If keys ARE configured => strict match.
    """
    key = _norm(api_key)
    if not key:
        raise ValueError("Missing API key")

    configured = _configured_keys()

    # Local dev fallback: no keys configured => accept any key
    if not configured:
        return FREE

    # Strict matching when configured
    if key == configured.get("master"):
        return BUSINESS
    if key == configured.get("business"):
        return BUSINESS
    if key == configured.get("pro"):
        return PRO
    if key == configured.get("free"):
        return FREE

    raise ValueError("Invalid API key")
