# tests/conftest.py
"""Pytest configuration and fixtures for thinkube-control backend tests."""

import pytest
import asyncio
from typing import Generator, AsyncGenerator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app import create_app
from app.db.session import Base, get_db
from app.core.config import settings

# Use the real PostgreSQL database for testing
# Tests run in the cluster and can access PostgreSQL
from app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def test_db() -> Generator[Session, None, None]:
    """Create a test database session for the main thinkube_control database.

    This database is used for:
    - API token authentication
    - User session management
    - Core application data
    """
    # Use PostgreSQL settings
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_size=5)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    yield db

    # Cleanup - rollback any changes made during the test
    db.rollback()
    db.close()
    # Note: We don't drop tables in PostgreSQL - they persist across tests


@pytest.fixture(scope="function")
def app():
    """Create a fresh FastAPI app instance for each test."""
    return create_app()


@pytest.fixture(scope="function")
def client(app, test_db: Session) -> Generator[TestClient, None, None]:
    """Create a test client with overridden database dependency."""

    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def mock_keycloak_token():
    """Mock Keycloak access token for testing."""
    return {
        "access_token": "mock_access_token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }


@pytest.fixture
def mock_user():
    """Mock authenticated user."""
    from app.core.security import User

    return User(
        sub="test-user-id",
        preferred_username="testuser",
        email="test@example.com",
        name="Test User",
        realm_access={"roles": ["user", "admin"]},
    )


@pytest.fixture
def auth_headers(mock_keycloak_token):
    """Authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {mock_keycloak_token['access_token']}"}


@pytest.fixture
def api_token_headers():
    """Authorization headers with API token."""
    return {"Authorization": "Bearer tk_test_token_123"}


# 🤖 Generated with Claude
