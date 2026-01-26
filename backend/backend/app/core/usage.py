from datetime import datetime, timezone
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.app.db import SessionLocal

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

class UsageEnforcerMiddleware(BaseHTTPMiddleware):
    """
    Enforces daily quota per API key using Postgres atomic upsert.
    Assumes request.state.api_key is set by require_api_key dependency.
    """

    def __init__(self, app, protected_prefixes: list[str] | None = None):
        super().__init__(app)
        self.protected_prefixes = protected_prefixes or ["/policies"]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or ""

        # Only enforce for protected route prefixes
        if not any(path.startswith(p) for p in self.protected_prefixes):
            return await call_next(request)

        api_key = getattr(request.state, "api_key", None)
        if not api_key:
            # If route is protected but auth didn't run, block.
            return JSONResponse({"detail": "Missing API key context"}, status_code=401)

        quota = int(getattr(api_key, "daily_quota", 0) or 0)
        if quota <= 0:
            return JSONResponse({"detail": "Quota not configured"}, status_code=403)

        day = _today_utc()

        db: Session = SessionLocal()
        try:
            # Atomic increment
            # Returns new count
            q = text("""
                INSERT INTO usage_daily (api_key_id, day, count)
                VALUES (:api_key_id, :day, 1)
                ON CONFLICT (api_key_id, day)
                DO UPDATE SET count = usage_daily.count + 1, updated_at = now()
                RETURNING count
            """)
            new_count = db.execute(q, {"api_key_id": api_key.id, "day": day}).scalar_one()
            db.commit()

            if new_count > quota:
                return JSONResponse(
                    {
                        "detail": "Daily quota exceeded",
                        "quota": quota,
                        "used": new_count,
                        "day": day,
                    },
                    status_code=429,
                )

        finally:
            db.close()

        return await call_next(request)
