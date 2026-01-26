import os
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

def tier_limit_provider(request) -> str:
    """
    SaaS-grade daily usage limits.
    Free is intentionally low to encourage upgrades.
    """

    default_free = os.getenv("DL_LIMIT_FREE", "20/day")
    default_pro = os.getenv("DL_LIMIT_PRO", "300/day")
    default_business = os.getenv("DL_LIMIT_BUSINESS", "3000/day")

    plan = getattr(request.state, "plan", None)
    plan_name = getattr(plan, "name", None) if plan else None
    plan_name = (plan_name or "free").lower()

    if plan_name == "business":
        return default_business
    if plan_name == "pro":
        return default_pro

    return default_free
