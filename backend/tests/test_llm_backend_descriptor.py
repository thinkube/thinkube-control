#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""spec.llm_backend reaches the service ConfigMap as the gateway reads it.

A gateway backend type is declared by its template, not listed in Thinkube
Control: the deploy validates the block and writes the resolved descriptor,
with the base Deployment, port and health path, into the service ConfigMap.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from service_config import ServiceConfigRequest, service_data  # noqa: E402
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
    return service_data(ServiceConfigRequest(**{**REQUEST, **overrides}))["service"]


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
        "gpu_compute_capabilities": [],
        "anthropic_messages": False,
        "startup_seconds": 600,
    }


def test_the_descriptor_carries_the_containers_start_window():
    containers = [{**REQUEST["containers"][0], "startupSeconds": 3600}]
    assert _service(llm_backend=BACKEND, containers=containers)["llm_backend"]["startup_seconds"] == 3600


def test_the_descriptor_carries_the_gpus_the_server_runs_on():
    backend = {**BACKEND, "gpu_compute_capabilities": ["12.0", "12.1"]}
    assert _service(llm_backend=backend)["llm_backend"]["gpu_compute_capabilities"] == ["12.0", "12.1"]


def test_a_service_without_the_block_has_no_descriptor():
    assert "llm_backend" not in _service()


def test_a_container_without_a_port_is_refused():
    with pytest.raises(ValueError, match="names no container with a port"):
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


def test_a_compute_capability_is_major_dot_minor():
    [error] = validate_llm_backend(_config(backend={**BACKEND, "gpu_compute_capabilities": ["sm_121"]}))
    assert "'sm_121'" in error


def test_the_serving_container_must_have_a_port():
    [error] = validate_llm_backend(_config(containers=[{"name": "inference", "build": "."}]))
    assert "must declare a port" in error
