from dataclasses import dataclass

@dataclass(frozen=True)
class Plan:
    name: str
    display_name: str
    daily_limit: int
    upgrade_hint: str

# Minimal default plan (safe)
FREE = Plan(
    name="free",
    display_name="Free",
    daily_limit=30,
    upgrade_hint="Upgrade to increase your daily limit."
)

# Very simple resolver (you can improve later)
def resolve_plan_from_api_key(api_key: str) -> Plan:
    # If you later add a DB lookup or env-based plans, do it here.
    return FREE
