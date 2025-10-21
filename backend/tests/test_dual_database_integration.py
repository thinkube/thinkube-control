# tests/test_dual_database_integration.py
"""Integration tests to verify dual database architecture works correctly."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime
import uuid

from app.core.api_tokens import APIToken, create_api_token
from app.models.cicd import Pipeline, PipelineStatus
from app.db.cicd_session import get_cicd_db


def test_dual_database_independence(
    client: TestClient, test_db: Session, test_cicd_db: Session, mock_user
):
    """Test that both databases work independently without interference."""
    from app.core.security import get_current_user
    from app.core.api_tokens import get_current_user_dual_auth

    # Override auth dependency
    async def override_get_current_user():
        return mock_user

    async def override_get_current_user_dual_auth():
        return {
            "sub": mock_user.sub,
            "preferred_username": mock_user.preferred_username,
            "email": mock_user.email,
            "name": mock_user.name,
            "realm_access": mock_user.realm_access,
            "auth_method": "keycloak",
        }

    client.app.dependency_overrides[get_current_user] = override_get_current_user
    client.app.dependency_overrides[get_current_user_dual_auth] = (
        override_get_current_user_dual_auth
    )
    client.app.dependency_overrides[get_cicd_db] = lambda: test_cicd_db

    # Test 1: Create an API token in the main database
    token_response = client.post(
        "/api/v1/tokens",
        json={
            "name": "test-integration-token",
            "expires_in_days": 30,
            "scopes": ["read", "write"],
        },
        headers={"Authorization": "Bearer mock_token"},
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert token_data["name"] == "test-integration-token"

    # Verify token exists in main database
    token_in_db = (
        test_db.query(APIToken).filter(APIToken.id == token_data["id"]).first()
    )
    assert token_in_db is not None
    assert token_in_db.name == "test-integration-token"

    # Test 2: Create a pipeline in the CI/CD database
    # Use the argo-workflow source-specific endpoint for testing
    pipeline_response = client.post(
        "/api/v1/cicd/pipelines/argo-workflow",
        json={
            "appName": "test-app",
            "branch": "main",
            "commitSha": "abc123def456",
            "commitMessage": "Test commit",
            "authorEmail": "test@example.com",
            "webhookTimestamp": datetime.utcnow().isoformat()
            + "Z",  # ISO8601 format for argo-workflow endpoint
            "triggerType": "manual",
            "workflowUid": "test-workflow-uid-123",
        },
        headers={"Authorization": "Bearer mock_token"},
    )
    assert pipeline_response.status_code == 200
    pipeline_data = pipeline_response.json()
    pipeline_id = pipeline_data["id"]

    # Verify pipeline exists in CI/CD database
    pipeline_in_db = (
        test_cicd_db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    )
    assert pipeline_in_db is not None
    assert pipeline_in_db.app_name == "test-app"

    # Test 3: Verify isolation - API token table doesn't exist in CI/CD database
    # We can verify this by checking that Pipeline table exists but APIToken doesn't
    # Note: We can't directly query APIToken from CI/CD db as it uses a different Base class

    # Test 4: List tokens and pipelines to ensure each uses correct database
    tokens_response = client.get(
        "/api/v1/tokens", headers={"Authorization": "Bearer mock_token"}
    )
    assert tokens_response.status_code == 200
    tokens = tokens_response.json()
    assert len(tokens) > 0
    assert any(t["name"] == "test-integration-token" for t in tokens)

    pipelines_response = client.get(
        "/api/v1/cicd/pipelines", headers={"Authorization": "Bearer mock_token"}
    )
    assert pipelines_response.status_code == 200
    pipelines_data = pipelines_response.json()
    assert len(pipelines_data["pipelines"]) > 0
    assert any(p["appName"] == "test-app" for p in pipelines_data["pipelines"])

    # Clean up
    del client.app.dependency_overrides[get_current_user]
    del client.app.dependency_overrides[get_current_user_dual_auth]
    del client.app.dependency_overrides[get_cicd_db]


def test_cicd_database_health_check(client: TestClient, test_cicd_db: Session):
    """Test that CI/CD health check uses the correct database."""
    from app.db.cicd_session import get_cicd_db

    # Override CI/CD database dependency
    client.app.dependency_overrides[get_cicd_db] = lambda: test_cicd_db

    response = client.get("/api/v1/cicd/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "cicd-monitoring"
    assert data["database"] == "healthy"

    # Clean up
    del client.app.dependency_overrides[get_cicd_db]


def test_database_rollback_isolation(
    client: TestClient, test_db: Session, test_cicd_db: Session, mock_user
):
    """Test that rollbacks in one database don't affect the other."""
    from app.core.security import get_current_user
    from app.core.api_tokens import get_current_user_dual_auth

    # Override dependencies
    async def override_get_current_user():
        return mock_user

    async def override_get_current_user_dual_auth():
        return {
            "sub": mock_user.sub,
            "preferred_username": mock_user.preferred_username,
            "email": mock_user.email,
            "name": mock_user.name,
            "realm_access": mock_user.realm_access,
            "auth_method": "keycloak",
        }

    client.app.dependency_overrides[get_current_user] = override_get_current_user
    client.app.dependency_overrides[get_current_user_dual_auth] = (
        override_get_current_user_dual_auth
    )
    client.app.dependency_overrides[get_cicd_db] = lambda: test_cicd_db

    # Create a token in main database
    token_response = client.post(
        "/api/v1/tokens",
        json={"name": "rollback-test-token", "expires_in_days": 30, "scopes": ["read"]},
        headers={"Authorization": "Bearer mock_token"},
    )
    assert token_response.status_code == 200
    token_id = token_response.json()["id"]

    # Create a pipeline in CI/CD database but don't commit yet
    pipeline = Pipeline(
        id=uuid.uuid4(),
        app_name="rollback-test",
        branch="main",
        commit_sha="test123",
        status=PipelineStatus.RUNNING,
    )
    test_cicd_db.add(pipeline)
    test_cicd_db.flush()  # Make it visible in this session but don't commit

    # Verify pipeline exists in current session
    pipeline_exists_before = (
        test_cicd_db.query(Pipeline).filter(Pipeline.id == pipeline.id).first()
    )
    assert pipeline_exists_before is not None

    # Rollback CI/CD database
    test_cicd_db.rollback()

    # Verify token still exists in main database
    token_exists = test_db.query(APIToken).filter(APIToken.id == token_id).first()
    assert token_exists is not None

    # Verify pipeline was rolled back in CI/CD database
    pipeline_exists = (
        test_cicd_db.query(Pipeline).filter(Pipeline.id == pipeline.id).first()
    )
    assert pipeline_exists is None

    # Clean up
    del client.app.dependency_overrides[get_current_user]
    del client.app.dependency_overrides[get_current_user_dual_auth]
    del client.app.dependency_overrides[get_cicd_db]


# 🤖 Generated with Claude
