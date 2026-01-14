import os
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import Base, engine
from app.db import models  # noqa: F401

log = logging.getLogger("data-lawyer.db")


def init_db() -> None:
    """
    Creates tables if DB is reachable.
    In production we should migrate (Alembic), but for now:
    - DO NOT crash the app if DB is temporarily unavailable.
    - Optionally control auto-create via AUTO_CREATE_TABLES env var.
    """
    auto_create = os.getenv("AUTO_CREATE_TABLES", "true").lower() in {"1", "true", "yes"}

    try:
        # quick connectivity ping
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("DB ping OK")
    except SQLAlchemyError as e:
        log.exception("DB ping failed during startup (app will still start): %s", e)
        return

    if not auto_create:
        log.info("AUTO_CREATE_TABLES disabled; skipping Base.metadata.create_all")
        return

    try:
        Base.metadata.create_all(bind=engine)
        log.info("DB tables ensured (create_all complete)")
    except SQLAlchemyError as e:
        log.exception("create_all failed (app will still start): %s", e)
