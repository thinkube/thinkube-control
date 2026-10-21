#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""spec.llm_backend reaches the service ConfigMap as the gateway reads it.

A gateway backend type is declared by its template, not listed in Thinkube
Control: the deploy validates the block and writes the resolved descriptor,
with the base Deployment, port and health path, into the service ConfigMap.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.service_discovery_config import (
    ServiceDiscoveryConfigRequest,
    generate_service_discovery_yaml,
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from thinkube_yaml_validator import validate_llm_backend  # noqa: E402

BACKEND = {
    "type": "vllm",
    "display_name": "vLLM",
    "container": "inference",
    "driver": "per-model-pod",
    "tier": "performance",
}

REQUEST = {
    "app_name": "vllm",
    "app_host": "vllm.example.com",
    "k8s_namespace": "vllm",
    "template_url": "https://example.com/t",
    "template_version": "v0.1.1",
    "deployment_date": "2026-01-01T00:00:00",
    "containers": [{"name": "inference", "build": ".", "port": 7860, "health": "/health"}],
    "deployment": {"type": "component", "name": "vllm", "replicas": 0, "gateway_managed": True},
}


def _service(**overrides):
    request = ServiceDiscoveryConfigRequest(**{**REQUEST, **overrides})
    return asyncio.run(generate_service_discovery_yaml(request, current_user={}))["service_data"]["service"]


def test_the_descriptor_names_the_base_deployment_port_and_health_path():
    assert _service(llm_backend=BACKEND)["llm_backend"] == {
        "type": "vllm",
        "display_name": "vLLM",
        "driver": "per-model-pod",
        "tier": "performance",
        "deployment": "vllm-inference",
        "port": 7860,
        "api_path": "/v1",
        "health_path": "/health",
    }


def test_a_service_without_the_block_has_no_descriptor():
    assert "llm_backend" not in _service()


def test_a_container_without_a_port_is_refused():
    with pytest.raises(HTTPException):
        _service(llm_backend=BACKEND, containers=[{"name": "inference"}])


def _config(backend=None, deployment=None, containers=None):
    return {
        "spec": {
            "deployment": deployment or REQUEST["deployment"],
            "containers": containers or REQUEST["containers"],
            "llm_backend": backend or BACKEND,
        }
    }


def test_a_complete_block_passes():
    assert validate_llm_backend(_config()) == []


def test_no_block_passes():
    assert validate_llm_backend({"spec": {}}) == []


def test_an_app_cannot_be_a_backend():
    [error] = validate_llm_backend(_config(deployment={"type": "app"}))
    assert "gateway_managed: true" in error


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("driver", "shared", "driver 'shared'"),
        ("tier", "fast", "tier 'fast'"),
        ("type", "vLLM", "type 'vLLM'"),
        ("container", "server", "names no container"),
    ],
)
def test_a_wrong_field_is_named(field, value, expected):
    [error] = validate_llm_backend(_config(backend={**BACKEND, field: value}))
    assert expected in error


def test_the_serving_container_must_have_a_port():
    [error] = validate_llm_backend(_config(containers=[{"name": "inference", "build": "."}]))
    assert "must declare a port" in error
