import os
from typing import Optional, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

# ============================================================
# DB URL handling
# ============================================================

def _db_url() -> Optional[str]:
    """
    Returns DATABASE_URL if set, else None.

    IMPORTANT: We do NOT raise here.
    This allows the API to boot even when DB is not configured/down.
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return None

    # Some providers still use postgres:// (SQLAlchemy expects postgresql://)
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    return url


# ============================================================
# Lazy engine + session (do not connect at import time)
# ============================================================

_ENGINE: Optional[Engine] = None
_SessionLocal = None

def get_engine() -> Optional[Engine]:
    """
    Lazily create and cache the SQLAlchemy engine.
    Returns None if DATABASE_URL is not configured.
    """
    global _ENGINE, _SessionLocal

    if _ENGINE is not None:
        return _ENGINE

    url = _db_url()
    if not url:
        return None

    # Supabase/managed Postgres often requires SSL.
    connect_args = {}
    if url.startswith("postgresql://"):
        # psycopg2 expects sslmode
        connect_args = {"sslmode": os.getenv("DB_SSLMODE", "require")}

    _ENGINE = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
        connect_args=connect_args,
    )

    _SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
    return _ENGINE


def db_ready() -> bool:
    """
    Quick DB health check (fast).
    Returns False if DB isn't configured or isn't reachable.
    """
    eng = get_engine()
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for routes that need DB.

    If DB is not configured, raises a clear error *only when a DB route is hit*,
    not during app startup.
    """
    eng = get_engine()
    if eng is None or _SessionLocal is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL in the environment (Render)."
        )

    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
