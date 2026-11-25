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

from app.main import app
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

@pytest.fixture(autouse=True)
def holds(monkeypatch):
    """The Scheduler, as the gateway asks it for the GPUs of a pod: every hold asked is given GPU-0 at once, unless
    the test sets `refuse` (the reason no GPU is given) or `status` (what a hold is read as)."""
    from types import SimpleNamespace

    from app.services import gpu_holds

    seen = SimpleNamespace(asked=[], ended=[], released=[], resized=[], refuse=None, status="running",
                           gpu_ids=["GPU-0"], gpus=1, gpu_memory_gb=4.0, refuse_resize=None)

    async def ask(name, node, gpus, gpu_memory_gb, memory_gb=0, on_gpus=None):
        seen.asked.append({"name": name, "node": node, "gpus": gpus, "gpu_memory_gb": gpu_memory_gb,
                           "memory_gb": memory_gb, "on_gpus": on_gpus})
        return {"id": f"hold-{len(seen.asked)}", "status": "waiting"}

    async def granted(hold_id):
        if seen.refuse:
            seen.ended.append(hold_id)
            raise gpu_holds.HoldError(f"the Scheduler gave it no GPU: {seen.refuse}")
        return list(seen.gpu_ids)

    def read(hold_id):
        return {"id": hold_id, "status": seen.status, "gpu_ids": list(seen.gpu_ids), "gpus": seen.gpus,
                "gpu_memory_gb": seen.gpu_memory_gb}

    def resize_now(hold_id, gpu_memory_gb):
        if seen.refuse_resize:
            raise gpu_holds.HoldError(f"the Scheduler refused to give the hold {hold_id} {gpu_memory_gb:g} GiB on "
                                      f"each GPU: 409 {seen.refuse_resize}")
        seen.resized.append((hold_id, gpu_memory_gb))

    async def resize(hold_id, gpu_memory_gb):
        resize_now(hold_id, gpu_memory_gb)

    async def get(hold_id):
        return read(hold_id)

    async def end(hold_id):
        seen.ended.append(hold_id)

    async def release(hold_id):
        seen.released.append(hold_id)

    monkeypatch.setattr(gpu_holds, "ask", ask)
    monkeypatch.setattr(gpu_holds, "granted", granted)
    monkeypatch.setattr(gpu_holds, "get", get)
    monkeypatch.setattr(gpu_holds, "end", end)
    monkeypatch.setattr(gpu_holds, "release", release)
    monkeypatch.setattr(gpu_holds, "get_sync", read)
    monkeypatch.setattr(gpu_holds, "end_sync", seen.ended.append)
    monkeypatch.setattr(gpu_holds, "release_sync", seen.released.append)
    monkeypatch.setattr(gpu_holds, "resize", resize)
    monkeypatch.setattr(gpu_holds, "resize_sync", resize_now)
    seen.listed = []
    monkeypatch.setattr(gpu_holds, "list_sync", lambda: list(seen.listed))
    return seen


@pytest.fixture(autouse=True)
def llm_backend_types_installed(monkeypatch):
    """The backend types the platform ships, as their service ConfigMaps declare them."""
    from app.services.llm_backend_types import BackendType, llm_backend_types

    def per_model(type_, display_name, namespace, deployment):
        return BackendType(
            type=type_, display_name=display_name, namespace=namespace, deployment=deployment,
            port=7860, api_path="/v1", health_path="/health", driver="per-model-pod",
            tier="performance",
        )

    types = {
        "vllm": per_model("vllm", "vLLM", "vllm", "vllm-inference"),
        "tensorrt-llm": per_model("tensorrt-llm", "TensorRT-LLM", "tensorrt", "tensorrt-inference"),
        "text-embeddings": per_model(
            "text-embeddings", "Text Embeddings", "text-embeddings", "text-embeddings-inference"
        ),
        "ollama": BackendType(
            type="ollama", display_name="Ollama", namespace="ollama", deployment="ollama",
            port=11434, api_path="/v1", health_path="/", driver="ollama", tier="flexible",
        ),
    }
    monkeypatch.setattr(llm_backend_types, "_types", types)
    monkeypatch.setattr(llm_backend_types, "refresh", lambda: len(types))
    return types


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