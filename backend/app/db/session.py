# app/db/session.py
"""Database session management."""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
import os

from app.core.config import settings

# Create base class for models
Base = declarative_base()

# Lazy initialization of engine and session
_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the database engine."""
    global _engine
    if _engine is None:
        # For tests, use SQLite if DATABASE_URL starts with sqlite
        if os.getenv("TESTING") or (
            hasattr(settings, "DATABASE_URL")
            and settings.DATABASE_URL.startswith("sqlite")
        ):
            _engine = create_engine(
                settings.DATABASE_URL,
                connect_args=(
                    {"check_same_thread": False}
                    if "sqlite" in settings.DATABASE_URL
                    else {}
                ),
            )
        else:
            _engine = create_engine(
                settings.DATABASE_URL,
                pool_pre_ping=True,  # Verify connections before using them
                pool_size=10,
                max_overflow=20,
            )
    return _engine


def get_session_local():
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine()
        )
    return _SessionLocal


# For backward compatibility
engine = get_engine
SessionLocal = get_session_local


def get_db() -> Generator[Session, None, None]:
    """
    Dependency to get database session.
    Ensures the session is closed after use.
    """
    session_factory = get_session_local()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


# 🤖 Generated with Claude
