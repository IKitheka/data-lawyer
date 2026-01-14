# app/db/session.py
import os
import logging
from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()
log = logging.getLogger("data-lawyer.db")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# NEVER print full DATABASE_URL (it contains credentials)
if DATABASE_URL:
    safe = DATABASE_URL.split("@")[-1]  # show only host/db part
    log.info("DATABASE_URL loaded (redacted): *@%s", safe)
else:
    # fallback ONLY for local dev
    DATABASE_URL = "sqlite:///./data_lawyer.db"
    log.warning("DATABASE_URL not set; using local sqlite db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,  # helps avoid stale connections on managed DBs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
