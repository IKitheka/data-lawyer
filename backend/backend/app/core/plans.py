from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Plan:
    name: str
    rpm: str  # e.g. "30/minute"

FREE = Plan("free", "30/minute")
PRO = Plan("pro", "120/minute")
BUSINESS = Plan("business", "600/minute")
ADMIN = Plan("admin", "2000/minute")

def _norm(s: str | None) -> str:
    return (s or "").strip()

def _truthy(s: str | None) -> bool:
    return _norm(s).lower() in ("1", "true", "yes", "y", "on")

def _split_keys(value: str | None) -> list[str]:
    """
    Supports:
      DL_API_KEY_FREE="k1,k2,k3"
      DL_API_KEY_FREE="k1"
    """
    raw = _norm(value)
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]

def configured_keys() -> dict[str, Plan]:
    """
    Map API key -> Plan using environment variables.

    Supported env vars (comma-separated allowed):
      DL_API_KEY_FREE
      DL_API_KEY_PRO
      DL_API_KEY_BUSINESS
      DL_API_KEY_ADMIN
      DL_API_KEY (optional master/admin key, comma-separated allowed)

    Optional:
      API_KEY_PREFIX_MODE=true
        - If enabled, accept keys prefixed with:
            free_, pro_, business_, admin_
        - Example: free_abcd1234 -> FREE
    """
    m: dict[str, Plan] = {}

    for k in _split_keys(os.getenv("DL_API_KEY_FREE")):
        m[k] = FREE
    for k in _split_keys(os.getenv("DL_API_KEY_PRO")):
        m[k] = PRO
    for k in _split_keys(os.getenv("DL_API_KEY_BUSINESS")):
        m[k] = BUSINESS
    for k in _split_keys(os.getenv("DL_API_KEY_ADMIN")):
        m[k] = ADMIN
    for k in _split_keys(os.getenv("DL_API_KEY")):
        m[k] = ADMIN

    return m

def resolve_plan_from_api_key(api_key: str) -> Plan:
    api_key = _norm(api_key)

    # Optional prefix mode (handy for testing without managing env keys)
    if _truthy(os.getenv("API_KEY_PREFIX_MODE")):
        low = api_key.lower()
        if low.startswith("free_"):
            return FREE
        if low.startswith("pro_"):
            return PRO
        if low.startswith("business_"):
            return BUSINESS
        if low.startswith("admin_"):
            return ADMIN

    keys = configured_keys()

    # Production switch: require configured keys
    if _truthy(os.getenv("REQUIRE_API_KEYS")):
        if not keys:
            raise ValueError("No API keys configured")
        plan = keys.get(api_key)
        if not plan:
            raise ValueError("Invalid API key")
        return plan

    # Dev fallback: accept any non-empty key as FREE if none configured
    if not keys:
        return FREE

    plan = keys.get(api_key)
    if not plan:
        raise ValueError("Invalid API key")
    return plan
