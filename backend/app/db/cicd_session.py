# app/db/cicd_session.py
"""Database session management for CI/CD monitoring database."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.core.config import settings

# Create engine for CI/CD monitoring database
_cicd_engine = None
_CICDSessionLocal = None


def get_cicd_engine():
    """Get or create the CI/CD database engine."""
    global _cicd_engine
    if _cicd_engine is None:
        # Construct URL for CI/CD monitoring database
        cicd_url = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.CICD_DB_NAME}"

        _cicd_engine = create_engine(
            cicd_url, pool_pre_ping=True, pool_size=10, max_overflow=20
        )
    return _cicd_engine


def get_cicd_session_local():
    """Get or create the CI/CD session factory."""
    global _CICDSessionLocal
    if _CICDSessionLocal is None:
        _CICDSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=get_cicd_engine()
        )
    return _CICDSessionLocal


def get_cicd_db() -> Generator[Session, None, None]:
    """
    Dependency to get CI/CD database session.
    Ensures the session is closed after use.
    """
    session_factory = get_cicd_session_local()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


# 🤖 Generated with Claude
