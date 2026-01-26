import hashlib
from fastapi import Header, HTTPException, Depends, Request
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import ApiKey

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def require_api_key(
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    raw = (x_api_key or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_hash = _hash_key(raw)

    row = db.query(ApiKey).filter(ApiKey.key_hash == key_hash, ApiKey.is_active == True).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # attach for limiter + usage middleware
    request.state.api_key = row
    request.state.plan = row.plan

    return row
