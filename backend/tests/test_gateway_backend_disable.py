#!/usr/bin/env python3
"""Tests that disabling a gateway-managed backend actually stops it.

A gateway-managed backend rests at zero replicas and serves from per-node
Deployments the gateway creates, so scaling the base Deployment reports success
and stops nothing. Placement has to honour the disabled state too, or the next
model load puts the pods straight back.
"""

from unittest.mock import MagicMock

import pytest

from app.models.services import Service
from app.services.k8s_manager import K8sServiceManager
from app.services.llm_pod_manager import (
    GATEWAY_LABEL,
    GATEWAY_LABEL_VALUE,
    LLMPodManager,
)


def deployment(name):
    d = MagicMock()
    d.metadata.name = name
    return d


@pytest.fixture
def manager():
    m = K8sServiceManager.__new__(K8sServiceManager)
    m.apps_v1 = MagicMock()
    m.scale_deployment = MagicMock(return_value=(True, None))
    m.cleanup_stale_pods = MagicMock()
    m.get_deployment_status = MagicMock(return_value={"replicas": 0})
    return m


def gateway_service():
    s = Service(
        name="vllm",
        type="component",
        namespace="vllm",
        resource_name="vllm-inference",
        service_metadata={"gateway_managed": True},
    )
    s.is_enabled = True
    return s


def test_disable_scales_the_gateway_deployments_not_the_base(manager):
    manager.apps_v1.list_namespaced_deployment.return_value.items = [
        deployment("vllm-inference-tkamd2"),
        deployment("vllm-inference-tkspark"),
    ]

    ok, error = K8sServiceManager.disable_service(manager, gateway_service())

    assert (ok, error) == (True, None)
    scaled = [c.args[1] for c in manager.scale_deployment.call_args_list]
    assert scaled == ["vllm-inference-tkamd2", "vllm-inference-tkspark"]
    assert "vllm-inference" not in scaled  # the resting base Deployment
    assert all(c.args[2] == 0 for c in manager.scale_deployment.call_args_list)


def test_disable_selects_only_gateway_created_deployments(manager):
    manager.apps_v1.list_namespaced_deployment.return_value.items = []

    K8sServiceManager.disable_service(manager, gateway_service())

    _, kwargs = manager.apps_v1.list_namespaced_deployment.call_args
    assert kwargs["label_selector"] == f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}"


def test_disable_reports_failure_when_a_scale_fails(manager):
    manager.apps_v1.list_namespaced_deployment.return_value.items = [
        deployment("vllm-inference-tkamd2")
    ]
    manager.scale_deployment.return_value = (False, "boom")

    ok, error = K8sServiceManager.disable_service(manager, gateway_service())

    assert ok is False
    assert error == "boom"


def test_ordinary_service_still_scales_its_own_deployment(manager):
    todo = Service(
        name="todo", type="user_app", namespace="todo",
        resource_name="todo-backend", service_metadata={},
    )
    todo.is_enabled = True

    K8sServiceManager.disable_service(manager, todo)

    scaled = [c.args[1] for c in manager.scale_deployment.call_args_list]
    assert scaled == ["todo-backend"]
    manager.apps_v1.list_namespaced_deployment.assert_not_called()


def test_enable_leaves_a_gateway_backend_at_rest(manager):
    """Its base Deployment must stay at zero; the gateway places pods on load."""
    service = gateway_service()
    service.is_enabled = False
    service.original_replicas = 1

    ok, error = K8sServiceManager.enable_service(manager, service)

    assert (ok, error) == (True, None)
    manager.scale_deployment.assert_not_called()


def test_enable_restores_an_ordinary_service(manager):
    todo = Service(
        name="todo", type="user_app", namespace="todo",
        resource_name="todo-backend", service_metadata={},
    )
    todo.is_enabled = False
    todo.original_replicas = 2

    K8sServiceManager.enable_service(manager, todo)

    manager.scale_deployment.assert_called_once_with("todo", "todo-backend", 2)


@pytest.mark.asyncio
async def test_placement_is_refused_while_the_backend_is_disabled(monkeypatch):
    pods = LLMPodManager()
    monkeypatch.setattr(pods, "_is_disabled", lambda backend_type: True)

    ok, managed = await pods.ensure_pod("vllm", "tkamd2")

    assert ok is False
    assert managed is None


def test_a_lookup_failure_does_not_block_placement(monkeypatch):
    """A database problem must not silently stop the gateway working."""
    pods = LLMPodManager()

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.session.SessionLocal", boom)

    assert pods._is_disabled("vllm") is False
