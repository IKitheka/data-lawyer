# backend/app/core/plans.py
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Plan:
    name: str
    daily_limit: str      # SlowAPI limit string format, e.g. "20/day"
    display_name: str
    upgrade_hint: str

FREE = Plan(
    name="free",
    daily_limit=os.getenv("FREE_DAILY_LIMIT", "20/day"),
    display_name="Free",
    upgrade_hint="Upgrade to Pro for more daily analyses."
)

PRO = Plan(
    name="pro",
    daily_limit=os.getenv("PRO_DAILY_LIMIT", "300/day"),
    display_name="Pro",
    upgrade_hint="Upgrade to Business for team & high-volume usage."
)

BUSINESS = Plan(
    name="business",
    daily_limit=os.getenv("BUSINESS_DAILY_LIMIT", "5000/day"),
    display_name="Business",
    upgrade_hint="Need more? Contact us for Enterprise limits."
)

def resolve_plan_from_api_key(api_key: str) -> Plan:
    """
    Map X-API-Key -> plan.
    For now, use env vars:
      DL_API_KEY_FREE, DL_API_KEY_PRO, DL_API_KEY_BUSINESS
    Later: replace with DB/Stripe lookup.
    """
    free_key = os.getenv("DL_API_KEY_FREE", "").strip()
    pro_key = os.getenv("DL_API_KEY_PRO", "").strip()
    biz_key = os.getenv("DL_API_KEY_BUSINESS", "").strip()

    if api_key and api_key == biz_key and biz_key:
        return BUSINESS
    if api_key and api_key == pro_key and pro_key:
        return PRO
    if api_key and api_key == free_key and free_key:
        return FREE

    # default: treat unknown key as invalid
    raise ValueError("invalid_api_key")
