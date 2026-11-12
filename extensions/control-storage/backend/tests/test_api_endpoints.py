# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# tests/test_api_endpoints.py
"""Test API endpoints for the template application."""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime


def test_root_endpoint(client: TestClient):
    """Test GET /"""
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()


def test_health_endpoint(client: TestClient):
    """Test GET /health"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["component"] == "backend"
    assert "service" in data


# Auth endpoints
def test_auth_config(client: TestClient):
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
    from app.core.security import get_current_user
    
    # Create an async function that returns the mock user
    async def override_get_current_user():
        return mock_user
    
    # Override the dependency
    client.app.dependency_overrides[get_current_user] = override_get_current_user
    
    response = client.get("/api/v1/auth/userinfo", headers={"Authorization": "Bearer mock_token"})
    assert response.status_code == 200
    assert response.json()["preferred_username"] == mock_user.preferred_username
    
    # Clean up
    del client.app.dependency_overrides[get_current_user]



# 🤖 Generated with Claude