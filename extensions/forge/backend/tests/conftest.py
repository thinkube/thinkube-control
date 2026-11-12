# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# tests/conftest.py
"""Pytest configuration and fixtures for thinkube-control backend tests."""

import os
import uuid
from pathlib import Path

import pytest
import asyncio
from alembic import command
from alembic.config import Config
from typing import Generator, AsyncGenerator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app import app
from app.db.session import get_db
from app.core.config import settings

# Use the real PostgreSQL database for testing
# Tests run in the cluster and can access PostgreSQL
from app.core.config import settings
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
BACKEND = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def db_engine():
    """One connection pool for the whole run, on a schema of its own made by the migrations.

    Each run creates a fresh schema in the test database, applies the
    migrations to it, as a deploy does, and drops it at the end. The tests
    then run on the schema production has, every migration is checked on
    each run, and runs at the same time never share tables. PGOPTIONS sets
    the search path for every connection of the process, the app's own
    included.
    """
    schema = f"test_{uuid.uuid4().hex[:12]}"
    os.environ["PGOPTIONS"] = f"-c search_path={schema}"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_size=5)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        migrations = Config(str(BACKEND / "alembic.ini"))
        migrations.set_main_option("script_location", str(BACKEND / "alembic"))
        migrations.attributes["connection"] = connection
        command.upgrade(migrations, "head")
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
        del os.environ["PGOPTIONS"]


@pytest.fixture(scope="function")
def test_db(db_engine) -> Generator[Session, None, None]:
    """Every test owns its data.

    The test runs inside one transaction on its own connection, and that
    transaction is rolled back when the test ends. What the endpoints
    commit is committed to a savepoint inside it, so the code under test
    behaves exactly as it does in production and still nothing it wrote
    survives the test.

    This is what lets the same database serve many tests at once and many
    runs in a row: no test sees another test's rows, and a thousand runs
    leave the store as they found it.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()



@pytest.fixture(scope="function")
def client(test_db: Session) -> Generator[TestClient, None, None]:
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
        "expires_in": 3600
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
        realm_access={"roles": ["user", "admin"]}
    )

@pytest.fixture
def auth_headers(mock_keycloak_token):
    """Authorization headers for authenticated requests."""
    return {
        "Authorization": f"Bearer {mock_keycloak_token['access_token']}"
    }

@pytest.fixture
def api_token_headers():
    """Authorization headers with API token."""
    return {
        "Authorization": "Bearer tk_test_token_123"
    }

# 🤖 Generated with Claude