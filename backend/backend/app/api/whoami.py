from fastapi import APIRouter, Request, Depends
from backend.app.core.auth import require_api_key

router = APIRouter()

def _mask(k: str) -> str:
    k = (k or "").strip()
    if len(k) <= 8:
        return "***"
    return k[:4] + "…" + k[-4:]

@router.get("/whoami")
async def whoami(request: Request, plan=Depends(require_api_key)):
    api_key = getattr(request.state, "api_key", "") or ""
    client_id = request.headers.get("X-Client-Id", "")

    return {
        "ok": True,
        "client_id": client_id,
        "api_key_masked": _mask(api_key),
        "plan": getattr(plan, "name", "unknown"),
        "rpm": getattr(plan, "rpm", "unknown"),
    }
