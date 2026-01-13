from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import response 

from app.db.init_db import init_db
from app.api import policies, requests, health
from app.api.policies import router as policies_router

app = FastAPI(title="Data Lawyer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only; tighten later
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

@app.get("/")
def root():
    return {"message": "Data Lawyer API running"}

@app.head("/")
def root_head():
    return Response(status_code=200)
