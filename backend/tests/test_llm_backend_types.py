#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The gateway's backend types are read from the service ConfigMaps.

A component that declares spec.llm_backend gets a service.llm_backend block
in its ConfigMap; that block, and its namespace, is the whole backend type.
"""

from types import SimpleNamespace

import kubernetes
import pytest
import yaml

from app.services.llm_backend_types import LLMBackendTypes, parse_descriptor

VLLM = {
    "type": "vllm", "display_name": "vLLM", "driver": "per-model-pod", "tier": "performance",
    "deployment": "vllm-inference", "port": 7860, "api_path": "/v1", "health_path": "/health",
}


def configmap(namespace, service, name="thinkube-service-config"):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        data={"service.yaml": yaml.safe_dump({"service": service})},
    )


@pytest.fixture
def cluster(monkeypatch):
    items = []
    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: SimpleNamespace(
        list_config_map_for_all_namespaces=lambda label_selector: SimpleNamespace(items=items),
    ))
    return items


def test_a_descriptor_becomes_a_backend_type_in_its_namespace():
    backend = parse_descriptor("vllm", VLLM)
    assert (backend.type, backend.namespace, backend.deployment, backend.port) == (
        "vllm", "vllm", "vllm-inference", 7860
    )
    assert backend.performance and not backend.shared


def test_a_descriptor_missing_fields_names_them():
    with pytest.raises(ValueError, match="port, api_path"):
        parse_descriptor("vllm", {k: v for k, v in VLLM.items() if k not in ("port", "api_path")})


def test_an_unknown_driver_is_refused():
    with pytest.raises(ValueError, match="unknown driver 'shared'"):
        parse_descriptor("vllm", {**VLLM, "driver": "shared"})


def test_refresh_reads_only_services_that_declare_a_backend(cluster):
    cluster.append(configmap("vllm", {"name": "vllm", "llm_backend": VLLM}))
    cluster.append(configmap("todo", {"name": "todo"}))
    cluster.append(configmap("other", {"llm_backend": {**VLLM, "type": "x"}}, name="something-else"))
    types = LLMBackendTypes()
    assert types.refresh() == 1
    assert types.names() == ["vllm"]


def test_a_malformed_descriptor_leaves_only_that_backend_out(cluster):
    cluster.append(configmap("vllm", {"llm_backend": VLLM}))
    cluster.append(configmap("broken", {"llm_backend": {"type": "broken"}}))
    types = LLMBackendTypes()
    types.refresh()
    assert types.names() == ["vllm"]


def test_an_uninstalled_backend_leaves_the_list(cluster):
    cluster.append(configmap("vllm", {"llm_backend": VLLM}))
    types = LLMBackendTypes()
    types.refresh()
    cluster.clear()
    types.refresh()
    assert types.names() == []
    with pytest.raises(KeyError, match="'vllm' is not installed"):
        types.require("vllm")


def test_a_failed_read_keeps_the_known_types(cluster, monkeypatch):
    cluster.append(configmap("vllm", {"llm_backend": VLLM}))
    types = LLMBackendTypes()
    types.refresh()

    def down():
        raise RuntimeError("API server unreachable")

    monkeypatch.setattr(kubernetes.client, "CoreV1Api", down)
    types.refresh()
    assert types.names() == ["vllm"]
