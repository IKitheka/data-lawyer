# backend/app/main.py
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.limiter import limiter
from app.core.plans import resolve_plan_from_api_key, FREE

from app.api import policies, requests as reqs, health

app = FastAPI(title="Data Lawyer API", version="1.0.0")

# ✅ attach limiter + middleware
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ✅ CORS (tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 500 handler (keep for debugging)
@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "type": exc.__class__.__name__,
        },
    )

# ✅ Custom “upgrade style” 429 handler (THIS is what you wanted)
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, _exc: RateLimitExceeded):
    api_key = request.headers.get("x-api-key", "")
    try:
        plan = resolve_plan_from_api_key(api_key)
    except Exception:
        plan = FREE

    upgrade_url = "https://your-site.com/pricing"

    # NOTE: Retry-After is typically a response header, not a request header.
    # We'll provide a sensible default.
    return JSONResponse(
        status_code=429,
        content={
            "error": "RATE_LIMIT_REACHED",
            "message": f"You’ve reached your {plan.display_name} plan limit ({plan.daily_limit}).",
            "plan": plan.name,
            "limit": plan.daily_limit,
            "hint": plan.upgrade_hint,
            "upgrade_url": upgrade_url,
        },
        headers={"Retry-After": "3600"},
    )

# ✅ routers
app.include_router(policies.router, prefix="/policies", tags=["policies"])
app.include_router(reqs.router, prefix="/requests", tags=["requests"])
app.include_router(health.router, prefix="/health", tags=["health"])

@app.get("/")
def root():
    return {"message": "Data Lawyer API running"}
