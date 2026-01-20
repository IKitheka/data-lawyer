import os
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.app.core.limiter import limiter

app = FastAPI(title="Data Lawyer API", version="1.0.0")

# attach limiter + middleware
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# CORS (ok for extension testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": exc.__class__.__name__},
    )

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_request: Request, _exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "RATE_LIMIT_REACHED",
            "message": "Too many requests. Please retry later.",
        },
        headers={"Retry-After": "60"},
    )

# ✅ PROOF: verify what code is deployed
@app.get("/__version")
def __version():
    return {
        "ok": True,
        "service": "data-lawyer",
        "where": "backend/app/main.py",
        "env_port": os.getenv("PORT"),
    }

# ✅ PROOF: verify headers arriving at Render
@app.get("/__whoami")
async def __whoami(request: Request):
    return {
        "x_api_key": request.headers.get("x-api-key"),
        "x_client_id": request.headers.get("x-client-id"),
        "origin": request.headers.get("origin"),
        "host": request.headers.get("host"),
        "cf_ray": request.headers.get("cf-ray"),
    }

# ✅ Minimal analyze route (no auth yet, no rate limit yet)
@app.post("/policies/analyze")
async def analyze(payload: dict, request: Request):
    url = payload.get("url", "")
    text = payload.get("text", "") or ""
    return {
        "risk_level": "UNKNOWN",
        "risk_score": None,
        "summary": f"Received {len(text)} chars for {url}.",
        "red_flags": [],
        "jurisdiction": "UNKNOWN",
        "service_name": url,
        "llm_model": "none",
        "client_id": request.headers.get("x-client-id"),
    }
