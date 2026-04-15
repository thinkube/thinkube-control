# tests/test_api_endpoints.py
"""Test all API endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_root_endpoint(client: TestClient):
    """Test GET /"""
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()
    assert "Welcome to the K8s Dashboard Hub API" in response.json()["message"]


def test_health_endpoint(client: TestClient):
    """Test GET /health"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# Auth endpoints
def test_auth_info(client: TestClient):
    """Test GET /api/v1/auth/auth-config"""
    response = client.get("/api/v1/auth/auth-config")
    assert response.status_code == 200
    data = response.json()
    assert "keycloak_url" in data
    assert "realm" in data
    assert "client_id" in data


def test_userinfo_unauthenticated(client: TestClient):
    """Test GET /api/v1/auth/userinfo without auth"""
    response = client.get("/api/v1/auth/userinfo")
    assert response.status_code == 401


def test_userinfo_authenticated(client: TestClient, mock_user):
    """Test GET /api/v1/auth/userinfo with auth"""
    from app.core.api_tokens import get_current_user_dual_auth

    # Create an async function that returns the mock user
    async def override_get_current_user_dual_auth():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user_dual_auth] = override_get_current_user_dual_auth

    response = client.get(
        "/api/v1/auth/userinfo", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200
    assert response.json()["preferred_username"] == mock_user.preferred_username

    # Clean up
    del client.app.dependency_overrides[get_current_user_dual_auth]


# Dashboard endpoints
def test_dashboards_unauthenticated(client: TestClient):
    """Test GET /api/v1/dashboards without auth"""
    response = client.get("/api/v1/dashboards")
    assert response.status_code == 401


def test_dashboards_authenticated(client: TestClient, mock_user):
    """Test GET /api/v1/dashboards with auth"""
    from app.core.api_tokens import get_current_user_dual_auth

    # Create an async function that returns the mock user
    async def override_get_current_user_dual_auth():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user_dual_auth] = override_get_current_user_dual_auth

    response = client.get(
        "/api/v1/dashboards", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Clean up
    del client.app.dependency_overrides[get_current_user_dual_auth]


def test_dashboard_categories_unauthenticated(client: TestClient):
    """Test GET /api/v1/dashboards/categories without auth"""
    response = client.get("/api/v1/dashboards/categories")
    assert response.status_code == 401


def test_dashboard_categories_authenticated(client: TestClient, mock_user):
    """Test GET /api/v1/dashboards/categories with auth"""
    from app.core.security import get_current_user

    # Create an async function that returns the mock user
    async def override_get_current_user():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user] = override_get_current_user

    response = client.get(
        "/api/v1/dashboards/categories", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)

    # Clean up
    del client.app.dependency_overrides[get_current_user]



# Token endpoints
def test_tokens_list_unauthenticated(client: TestClient):
    """Test GET /api/v1/tokens without auth"""
    response = client.get("/api/v1/tokens")
    assert response.status_code == 401


def test_tokens_list_authenticated(client: TestClient, mock_user, test_db):
    """Test GET /api/v1/tokens with auth"""
    from app.core.security import get_current_user

    # Create an async function that returns the mock user
    async def override_get_current_user():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user] = override_get_current_user

    response = client.get(
        "/api/v1/tokens", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Clean up
    del client.app.dependency_overrides[get_current_user]


def test_token_create_unauthenticated(client: TestClient):
    """Test POST /api/v1/tokens without auth"""
    response = client.post("/api/v1/tokens", json={"name": "test-token"})
    assert response.status_code == 401


def test_token_create_authenticated(client: TestClient, mock_user, test_db):
    """Test POST /api/v1/tokens with auth"""
    from app.core.security import get_current_user

    # Create an async function that returns the mock user
    async def override_get_current_user():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user] = override_get_current_user

    response = client.post(
        "/api/v1/tokens",
        json={"name": "test-token", "expires_in_days": 30, "scopes": ["read"]},
        headers={"Authorization": "Bearer mock_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-token"
    assert "token" in data
    assert data["token"].startswith("tk_")

    # Clean up
    del client.app.dependency_overrides[get_current_user]


def test_token_revoke_authenticated(client: TestClient, mock_user, test_db):
    """Test DELETE /api/v1/tokens/{token_id} with auth"""
    from app.core.security import get_current_user

    # Create an async function that returns the mock user
    async def override_get_current_user():
        return mock_user

    # Override the dependency
    client.app.dependency_overrides[get_current_user] = override_get_current_user

    # First create a token
    create_response = client.post(
        "/api/v1/tokens",
        json={"name": "token-to-revoke", "expires_in_days": 30, "scopes": ["read"]},
        headers={"Authorization": "Bearer mock_token"},
    )
    assert create_response.status_code == 200
    data = create_response.json()
    assert "id" in data
    token_id = data["id"]

    # Then revoke it
    response = client.delete(
        f"/api/v1/tokens/{token_id}", headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200

    # Clean up
    del client.app.dependency_overrides[get_current_user]


# 🤖 Generated with Claude
