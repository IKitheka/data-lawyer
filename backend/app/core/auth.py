from fastapi import Header, HTTPException, Request

ALLOWED_ORIGINS = [
    "chrome-extension://",  # Chrome extensions
]

def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None)
):
    expected = os.getenv("DL_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="Server missing DL_API_KEY")

    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")

    origin = request.headers.get("origin", "")
    if origin and not any(origin.startswith(o) for o in ALLOWED_ORIGINS):
        raise HTTPException(status_code=403, detail="Forbidden origin")
