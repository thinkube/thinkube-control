# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

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
    assert data["status"] == "healthy"
    assert data["component"] == "backend"
    assert "service" in data

def test_cicd_health_endpoint(client: TestClient):
    """Test the CI/CD health check endpoint if monitoring is enabled."""
    # Skip this test if CI/CD monitoring is not enabled
    # In the template, this endpoint may not exist
    response = client.get("/api/v1/cicd/health")
    # If monitoring is disabled, endpoint should return 404
    # If enabled, it should return health status
    assert response.status_code in [200, 404]
    
    if response.status_code == 200:
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "cicd-monitoring"

# 🤖 Generated with Claude