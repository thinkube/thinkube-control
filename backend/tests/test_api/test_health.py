# tests/test_api/test_health.py
"""Tests for health check endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_root_endpoint(client: TestClient):
    """Test the root endpoint returns expected information."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs_url" in data
    assert data["docs_url"] == "/api/v1/docs"


def test_health_endpoint(client: TestClient):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_cicd_health_endpoint(client: TestClient, test_cicd_db):
    """Test the CI/CD health check endpoint."""
    from app.db.cicd_session import get_cicd_db

    # Override CI/CD database dependency
    client.app.dependency_overrides[get_cicd_db] = lambda: test_cicd_db

    response = client.get("/api/v1/cicd/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "cicd-monitoring"
    assert "database" in data
    assert data["database"] == "healthy"

    # Clean up
    del client.app.dependency_overrides[get_cicd_db]


# 🤖 Generated with Claude
