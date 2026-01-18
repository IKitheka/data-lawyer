# backend/app/core/auth.py
import os
from fastapi import Header, HTTPException

def require_api_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("DL_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="Server missing DL_API_KEY")

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
