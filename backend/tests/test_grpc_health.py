# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A gRPC endpoint is healthy when a gRPC server answers it.

Argo Workflows answers the health check with UNIMPLEMENTED and Argo CD with
UNAUTHENTICATED: both servers are up. No answer, or NOT_SERVING, is down.
"""

import asyncio

import grpc
import pytest
from grpc_health.v1 import health_pb2

import app.services.health_checker as hc


@pytest.mark.parametrize("url,target", [
    ("argocd.thinkube.com", "argocd.thinkube.com:443"),
    ("grpc-argo.thinkube.com", "grpc-argo.thinkube.com:443"),
    ("weaviate-grpc.thinkube.com:443", "weaviate-grpc.thinkube.com:443"),
    ("https://example.test:8443/path", "example.test:8443"),
])
def test_target(url, target):
    assert hc.grpc_target(url) == target


class RpcError(grpc.aio.AioRpcError):
    def __init__(self, code):
        super().__init__(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details="said so")


def answering(monkeypatch, answer):
    class Stub:
        def __init__(self, channel):
            pass

        async def Check(self, request, timeout):
            if isinstance(answer, Exception):
                raise answer
            return health_pb2.HealthCheckResponse(status=answer)

    monkeypatch.setattr(hc.health_pb2_grpc, "HealthStub", Stub)


def check(url="argocd.thinkube.com"):
    return asyncio.run(hc.check_grpc(url))


def test_serving_is_healthy(monkeypatch):
    answering(monkeypatch, health_pb2.HealthCheckResponse.SERVING)
    assert check()["status"] == "healthy"


def test_not_serving_is_unhealthy(monkeypatch):
    answering(monkeypatch, health_pb2.HealthCheckResponse.NOT_SERVING)
    result = check()
    assert result["status"] == "unhealthy"
    assert "NOT_SERVING" in result["error"]


@pytest.mark.parametrize("code", [grpc.StatusCode.UNIMPLEMENTED,
                                  grpc.StatusCode.UNAUTHENTICATED])
def test_a_server_that_answers_with_a_status_is_healthy(monkeypatch, code):
    answering(monkeypatch, RpcError(code))
    result = check()
    assert result["status"] == "healthy"
    assert result["details"]["code"] == code.name


@pytest.mark.parametrize("code", [grpc.StatusCode.UNAVAILABLE,
                                  grpc.StatusCode.DEADLINE_EXCEEDED])
def test_no_answer_is_unhealthy(monkeypatch, code):
    answering(monkeypatch, RpcError(code))
    result = check()
    assert result["status"] == "unhealthy"
    assert result["error"].startswith(f"argocd.thinkube.com:443: {code.name}")
