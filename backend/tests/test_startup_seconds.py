#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""containers[].startupSeconds sets how long a GPU container may take to start.

An inference server that loads a large model before it opens its port
answers its health path only after the load; the startup probe must wait
that long, or the pod is killed while it loads.
"""

import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from thinkube_yaml_validator import validate_startup_seconds  # noqa: E402

GPU = {"name": "inference", "build": ".", "port": 8000, "health": "/health",
       "size": "xlarge", "gpu": {"count": 1, "memory": "20Gi"}}


def startup_probe(container):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(REPO / "templates/k8s")),
        undefined=jinja2.StrictUndefined, lstrip_blocks=True, trim_blocks=True,
    )
    env.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    env.filters["to_json"] = lambda x: __import__("json").dumps(x)
    spec = {"spec": {"containers": [container], "deployment": {"type": "component", "name": "flare", "replicas": 0}}}
    text = env.get_template("deployment-separate.j2").render(
        project_name="flare", k8s_namespace="flare", domain_name="thinkube.com",
        container_registry="registry.thinkube.com", admin_username="tkadmin",
        thinkube_spec=spec, manifest_params={}, spec_env={}, seaweedfs_endpoint="http://seaweedfs:8333",
        deployment_env_from=[], system_username="thinkube", master_node_name="tkamd1",
        gitea_org="thinkube-deployments", build_architectures=["amd64", "arm64"],
    )
    [deployment] = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "Deployment"]
    return deployment["spec"]["template"]["spec"]["containers"][0]["startupProbe"]


def test_a_gpu_container_waits_ten_minutes_by_default():
    probe = startup_probe(GPU)
    assert probe["periodSeconds"] * probe["failureThreshold"] == 600


def test_startup_seconds_sets_the_window():
    probe = startup_probe({**GPU, "startupSeconds": 1800})
    assert probe["periodSeconds"] * probe["failureThreshold"] == 1800


def test_a_window_is_rounded_up_to_whole_probes():
    probe = startup_probe({**GPU, "startupSeconds": 1805})
    assert probe["failureThreshold"] == 181


@pytest.mark.parametrize("value", [5, "1800", True])
def test_startup_seconds_is_an_integer_of_at_least_10(value):
    [error] = validate_startup_seconds({"spec": {"containers": [{**GPU, "startupSeconds": value}]}})
    assert "at least 10" in error


def test_startup_seconds_needs_a_gpu_container_with_a_health_path():
    container = {"name": "web", "build": ".", "port": 8000, "startupSeconds": 1800}
    [error] = validate_startup_seconds({"spec": {"containers": [container]}})
    assert "needs gpu and health" in error
