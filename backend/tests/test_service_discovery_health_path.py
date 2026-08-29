#!/usr/bin/env python3
"""Tests for resolve_health_path (thinkube.yaml spec -> web endpoint health path).

The generated web endpoint points at the app host root, so its health check has
to use the health path of whichever container is routed at "/". Hardcoding
"/health" marks any app declaring a different path permanently unhealthy: an
Antora static site declaring `health: /` has no /health route and returns 404.
"""

import pytest

from app.api.service_discovery_config import (
    DEFAULT_HEALTH_PATH,
    Container,
    Route,
    resolve_health_path,
)


def test_root_route_selects_its_containers_health_path():
    containers = [Container(name="docs", health="/")]
    routes = [Route(path="/", to="docs")]

    assert resolve_health_path(containers, routes) == "/"


def test_multi_container_app_uses_the_container_routed_at_root():
    containers = [
        Container(name="backend", health="/api/health"),
        Container(name="frontend", health="/health"),
    ]
    routes = [Route(path="/api", to="backend"), Route(path="/", to="frontend")]

    assert resolve_health_path(containers, routes) == "/health"


def test_single_container_needs_no_routes():
    assert resolve_health_path([Container(name="app", health="/ping")], []) == "/ping"


@pytest.mark.parametrize(
    "containers,routes",
    [
        # Nothing declared at all.
        ([], []),
        # Container declares no health path.
        ([Container(name="app")], [Route(path="/", to="app")]),
        # Root route names a container that is not in the list.
        ([Container(name="app", health="/ping")], [Route(path="/", to="other")]),
        # Several containers and no root route to disambiguate them.
        ([Container(name="a", health="/ping"), Container(name="b")], []),
    ],
)
def test_falls_back_to_default_when_unresolvable(containers, routes):
    assert resolve_health_path(containers, routes) == DEFAULT_HEALTH_PATH


def test_routes_are_optional_for_callers():
    """Callers predating the routes field must keep the previous behaviour."""
    request_fields = {
        "app_name": "app",
        "app_host": "app.example.com",
        "k8s_namespace": "app",
        "template_url": "https://example.com/t",
        "deployment_date": "2026-01-01T00:00:00",
        "containers": [{"name": "app"}],
    }

    from app.api.service_discovery_config import ServiceDiscoveryConfigRequest

    request = ServiceDiscoveryConfigRequest(**request_fields)

    assert request.routes == []
    assert resolve_health_path(request.containers, request.routes) == DEFAULT_HEALTH_PATH
