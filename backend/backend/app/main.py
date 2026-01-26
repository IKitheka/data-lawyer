from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import policies

app = FastAPI(
    title="Data Lawyer API",
    description="Privacy Policy Risk Intelligence Engine",
    version="1.0.0",
)

# CORS:
# - For production, set ALLOWED_ORIGINS env var on Render (comma-separated).
# - Default "*" is fine for local dev, but tighten in prod.
import os
origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
allow_origins = ["*"] if origins_env == "*" else [o.strip() for o in origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(policies.router, prefix="/policies", tags=["Policy Analysis"])

@app.get("/health")
def health():
    return {"ok": True}
