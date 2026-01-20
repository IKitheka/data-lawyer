from fastapi import Header, HTTPException

# Simple API key auth for now.
# Later you can replace this with DB-backed keys / tiers.
VALID_KEYS = set(filter(None, [
    # optional: allow local env key
]))

def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """
    Accepts X-API-Key header and rejects missing/invalid keys.
    For now, if you don't want strict checking, you can return a dummy plan.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    # If you want to allow ANY key for now (to just make product work), comment this block.
    # Otherwise, keep it and add your key(s) below.
    allowed = {
        "af8d7e5a4eccc9a5e57306c92d5ba1257576150314e6921dbdb3034168c386f0"
    }
    if x_api_key not in allowed:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Return something truthy (your routes depend on it)
    return {"ok": True, "api_key": x_api_key}
