from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from redis.exceptions import AuthenticationError, ConnectionError as RedisConnectionError

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.db.init_db import init_db
from app.api import policies, requests, health
from app.core.limiter import limiter

app = FastAPI(title="Data Lawyer API", version="1.0.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

app.include_router(policies.router, prefix="/policies", tags=["policies"])
app.include_router(requests.router, prefix="/requests", tags=["requests"])
app.include_router(health.router, prefix="/health", tags=["health"])

@app.exception_handler(AuthenticationError)
async def redis_auth_error_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Redis auth failed for rate limiter. Fix REDIS_URL."},
    )

@app.exception_handler(RedisConnectionError)
async def redis_conn_error_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Redis connection failed for rate limiter. Check REDIS_URL/network."},
    )

@app.get("/")
def root():
    return {"message": "Data Lawyer API running"}
