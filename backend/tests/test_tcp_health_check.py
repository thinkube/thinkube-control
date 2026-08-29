#!/usr/bin/env python3
"""Tests for the tcp branch of HealthCheckService.check_endpoint_health.

Services that speak no HTTP (Valkey and friends) register tcp endpoints with no
health_url, so the check target is the endpoint url itself. A proxy sitting in
front of a dead backend still accepts the connection and then resets it, which
is why an immediate EOF has to count as unhealthy rather than healthy.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.services.health_checker import HealthCheckService


class FakeEndpoint:
    def __init__(self, url=None, health_url=None, type="tcp"):
        self.url = url
        self.health_url = health_url
        self.type = type


@pytest.fixture
def checker():
    return HealthCheckService(timeout=5)


@asynccontextmanager
async def listener(greeting=None, drop=False):
    """A throwaway TCP server on a free port, torn down without dangling tasks.

    greeting: bytes to send on connect, or None to stay silent.
    drop:     close the connection immediately, as a proxy with no upstream does.
    """
    release = asyncio.Event()

    async def handle(reader, writer):
        if drop:
            writer.close()
            return
        if greeting:
            writer.write(greeting)
            await writer.drain()
        await release.wait()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        release.set()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_silent_listener_is_healthy(checker):
    """Valkey says nothing until spoken to - holding the connection is enough."""
    async with listener() as port:
        result = await checker.check_endpoint_health(
            FakeEndpoint(url=f"127.0.0.1:{port}")
        )

    assert result["status"] == "healthy"
    assert result["details"]["check_type"] == "tcp_connect"


@pytest.mark.asyncio
async def test_greeting_listener_is_healthy(checker):
    """A service that answers immediately is obviously up."""
    async with listener(greeting=b"-NOAUTH Authentication required.\r\n") as port:
        result = await checker.check_endpoint_health(
            FakeEndpoint(url=f"127.0.0.1:{port}")
        )

    assert result["status"] == "healthy"


@pytest.mark.asyncio
async def test_accept_then_immediate_close_is_unhealthy(checker):
    """This is the proxy-with-no-upstream case a bare connect would misreport."""
    async with listener(drop=True) as port:
        result = await checker.check_endpoint_health(
            FakeEndpoint(url=f"127.0.0.1:{port}")
        )

    assert result["status"] == "unhealthy"
    assert "closed immediately" in result["error"]


@pytest.mark.asyncio
async def test_nothing_listening_is_unhealthy(checker):
    """Take a port, release it, then check it - nothing is listening."""
    async with listener() as port:
        pass

    result = await checker.check_endpoint_health(FakeEndpoint(url=f"127.0.0.1:{port}"))

    assert result["status"] == "unhealthy"
    assert "Cannot connect" in result["error"]


@pytest.mark.asyncio
async def test_health_url_wins_over_url(checker):
    async with listener() as port:
        result = await checker.check_endpoint_health(
            FakeEndpoint(url="127.0.0.1:1", health_url=f"127.0.0.1:{port}")
        )

    assert result["status"] == "healthy"
    assert result["details"]["target"] == f"127.0.0.1:{port}"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["no-port-here", "", "host:notaport"])
async def test_unparseable_target_is_unknown_not_unhealthy(checker, target):
    """A malformed endpoint is a config problem, not a down service."""
    result = await checker.check_endpoint_health(FakeEndpoint(url=target))

    assert result["status"] == "unknown"


@pytest.mark.asyncio
async def test_scheme_prefixed_target_is_parsed(checker):
    async with listener() as port:
        result = await checker.check_endpoint_health(
            FakeEndpoint(url=f"tcp://127.0.0.1:{port}")
        )

    assert result["status"] == "healthy"
