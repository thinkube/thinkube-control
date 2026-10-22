#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The discovery ConfigMap is a manifest in k8s/, which Argo CD applies.

It is rendered from thinkube.yaml and the deploy's values, so it exists once
the app is synced, whether or not Thinkube Control ran when the build ended. A
regeneration reads the deploy's values back from the ConfigMap in the cluster.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from service_config import (  # noqa: E402
    CONFIGMAP_KEY,
    configmap_name,
    manifest,
    recorded_deploy,
    request_for,
)

CONFIG = {
    "metadata": {"version": "0.1.2"},
    "spec": {
        "deployment": {"type": "component", "name": "llama-cpp", "replicas": 0, "gateway_managed": True},
        "containers": [{"name": "server", "build": ".", "port": 8080, "health": "/health"}],
        "llm_backend": {
            "type": "llama-cpp",
            "display_name": "llama.cpp",
            "container": "server",
            "driver": "per-model-pod",
            "tier": "flexible",
        },
    },
}

DEPLOY = {
    "template_url": "https://github.com/thinkube/tkt-llama-cpp",
    "template_version": "v0.1.1",
    "project_description": "llama.cpp server",
    "deployment_date": "2026-10-22T11:00:00",
}


def render(config=CONFIG, namespace="llama-cpp", extension=False, **deploy):
    request = request_for(
        app_name="llama-cpp", domain="example.com", namespace=namespace, config=config, **{**DEPLOY, **deploy}
    )
    return yaml.safe_load(manifest(request, extension=extension))


def test_the_manifest_is_the_labelled_configmap_discovery_reads():
    configmap = render()
    assert configmap["kind"] == "ConfigMap"
    assert configmap["metadata"]["name"] == "thinkube-service-config"
    assert configmap["metadata"]["namespace"] == "llama-cpp"
    assert configmap["metadata"]["labels"]["thinkube.io/managed"] == "true"
    assert configmap["metadata"]["labels"]["thinkube.io/service-name"] == "llama-cpp"


def test_the_label_and_the_body_give_the_same_type():
    configmap = render()
    service = yaml.safe_load(configmap["data"][CONFIGMAP_KEY])["service"]
    assert configmap["metadata"]["labels"]["thinkube.io/service-type"] == service["type"] == "component"


def test_the_llm_backend_reaches_the_gateway():
    service = yaml.safe_load(render()["data"][CONFIGMAP_KEY])["service"]
    assert service["llm_backend"]["deployment"] == "llama-cpp-server"
    assert service["llm_backend"]["port"] == 8080


def test_an_extension_names_its_configmap_after_itself():
    configmap = render(namespace="thinkube-control", extension=True)
    assert configmap["metadata"]["name"] == configmap_name("llama-cpp", True) == "llama-cpp-service-config"
    assert configmap["metadata"]["labels"]["app.kubernetes.io/name"] == "llama-cpp"


def test_a_regeneration_renders_the_same_configmap_from_the_recorded_values():
    first = render()
    recorded = recorded_deploy(first["data"], "ConfigMap llama-cpp/thinkube-service-config")
    assert recorded == DEPLOY
    assert render(**recorded) == first


def test_the_default_description_is_recorded_and_rendered_again_unchanged():
    first = render(project_description="")
    recorded = recorded_deploy(first["data"], "x")
    assert render(**recorded) == first


def test_a_configmap_without_the_record_is_refused_with_the_way_out():
    with pytest.raises(ValueError, match="redeploy_template"):
        recorded_deploy({}, "ConfigMap llama-cpp/thinkube-service-config")


def test_a_record_missing_a_value_names_it():
    configmap = render()
    service = yaml.safe_load(configmap["data"][CONFIGMAP_KEY])
    del service["service"]["metadata"]["template_version"]
    with pytest.raises(ValueError, match="template_version"):
        recorded_deploy({CONFIGMAP_KEY: yaml.dump(service)}, "x")
