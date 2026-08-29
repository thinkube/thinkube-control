#!/usr/bin/env python3
"""Tests that enable/disable scale the deployment that actually exists.

Services are rarely named after their deployment: todo runs todo-backend, vllm
runs vllm-inference. Scaling by service name addresses a workload that is not
there, which reports success and changes nothing.
"""

import pytest

from app.models.services import Service
from app.services.k8s_manager import K8sServiceManager


target = K8sServiceManager._target_deployment


@pytest.mark.parametrize(
    "service_name,resource_name,expected",
    [
        ("vllm", "vllm-inference", "vllm-inference"),
        ("text-embeddings", "text-embeddings-inference", "text-embeddings-inference"),
        ("todo", "todo-backend", "todo-backend"),
    ],
)
def test_uses_the_recorded_deployment_name(service_name, resource_name, expected):
    service = Service(name=service_name, type="component", resource_name=resource_name)

    assert target(service) == expected


def test_falls_back_to_the_service_name_when_none_recorded():
    service = Service(name="qdrant", type="optional", resource_name=None)

    assert target(service) == "qdrant"


def test_falls_back_when_the_recorded_name_is_empty():
    service = Service(name="qdrant", type="optional", resource_name="")

    assert target(service) == "qdrant"
