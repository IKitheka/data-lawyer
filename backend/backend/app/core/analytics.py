from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

# For now: simple console logger.
# Later: you can push events into Supabase or Postgres.

def log_event(name: str, payload: Dict[str, Any] | None = None) -> None:
    payload = payload or {}

    # Always add timestamp
    payload["ts"] = datetime.utcnow().isoformat() + "Z"

    # Print to logs (Render will capture it)
    if os.getenv("ENV", "production") != "test":
        print(f"[ANALYTICS] {name} :: {payload}")
