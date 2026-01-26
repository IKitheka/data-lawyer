import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import policies
from backend.app.api import billing
from backend.app.core.usage import UsageEnforcerMiddleware

app = FastAPI(
    title="Data Lawyer API",
    description="Privacy Policy Risk Intelligence Engine",
    version="1.0.0",
)

origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
allow_origins = ["*"] if origins_env == "*" else [o.strip() for o in origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Daily quota enforcement for protected endpoints
app.add_middleware(UsageEnforcerMiddleware, protected_prefixes=["/policies"])

app.include_router(policies.router, prefix="/policies", tags=["Policy Analysis"])
app.include_router(billing.router, prefix="/billing", tags=["Billing"])

@app.get("/health")
def health():
    return {"ok": True}
