import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def _db_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Render/Supabase often gives postgresql://... which psycopg2 supports.
    return url

engine = create_engine(_db_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
