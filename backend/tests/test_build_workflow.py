#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A container's buildSize sets the memory its build pod gets.

The build workflow template gives every build the same 4Gi unless the
container declares a buildSize; the value is a workflow parameter, so one
Argo template serves every container.
"""

import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

from thinkube_yaml_validator import validate_build_size  # noqa: E402


def render(containers, architectures=None):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    variables = dict(
        project_name="notes",
        k8s_namespace="notes",
        domain_name="thinkube.com",
        container_registry="registry.thinkube.com",
        gitea_org="thinkube-deployments",
        system_username="thinkube",
        master_node_name="tkamd1",
        thinkube_spec={"spec": {"containers": containers}},
    )
    if architectures:
        variables["build_architectures"] = architectures
    rendered = env.get_template("build-workflow.j2").render(**variables)
    [workflow] = [d for d in yaml.safe_load_all(rendered) if d]
    return workflow


def templates(workflow):
    return {t["name"]: t for t in workflow["spec"]["templates"]}


def build_tasks(workflow):
    """The build tasks of the pipeline, by name, with their parameters as a dict."""
    tasks = templates(workflow)["ci-cd-pipeline"]["dag"]["tasks"]
    return {
        t["name"]: {p["name"]: p["value"] for p in t["arguments"]["parameters"]}
        for t in tasks if t["name"].startswith("build-")
    }


BACKEND = {"name": "backend", "build": "./backend", "port": 8000, "health": "/health"}
FRONTEND = {"name": "frontend", "build": ".", "port": 3000, "health": "/", "size": "medium", "buildSize": "medium"}


def test_a_container_without_build_size_builds_in_4gi():
    tasks = build_tasks(render([BACKEND]))
    assert tasks["build-backend"]["build_memory"] == "4Gi"


def test_build_size_sets_the_build_memory_and_not_the_run_time_size():
    tasks = build_tasks(render([BACKEND, FRONTEND]))
    assert tasks["build-frontend"]["build_memory"] == "8Gi"
    assert tasks["build-backend"]["build_memory"] == "4Gi"


@pytest.mark.parametrize("build_size,memory", [
    ("small", "4Gi"), ("medium", "8Gi"), ("large", "16Gi"), ("xlarge", "32Gi"),
])
def test_each_build_size_has_its_memory(build_size, memory):
    tasks = build_tasks(render([{**BACKEND, "buildSize": build_size}]))
    assert tasks["build-backend"]["build_memory"] == memory


def test_every_architecture_build_of_a_container_gets_its_build_memory():
    tasks = build_tasks(render([FRONTEND], ["amd64", "arm64"]))
    assert tasks["build-frontend-amd64"]["build_memory"] == "8Gi"
    assert tasks["build-frontend-arm64"]["build_memory"] == "8Gi"


@pytest.mark.parametrize("architectures,template", [
    (None, "buildah-build"), (["amd64", "arm64"], "buildah-build-on-arch"),
])
def test_the_build_pod_limit_is_the_build_memory_parameter(architectures, template):
    workflow = render([FRONTEND], architectures)
    build = templates(workflow)[template]
    assert {p["name"] for p in build["inputs"]["parameters"]} >= {"build_memory"}
    assert build["container"]["resources"]["limits"]["memory"] == "{{inputs.parameters.build_memory}}"


def test_an_unknown_build_size_is_refused_with_the_allowed_values():
    config = {"spec": {"containers": [{**FRONTEND, "buildSize": "huge"}]}}
    [error] = validate_build_size(config)
    assert "buildSize 'huge'" in error
    assert "small, medium, large, xlarge" in error


def test_known_and_absent_build_sizes_pass():
    assert validate_build_size({"spec": {"containers": [BACKEND, FRONTEND]}}) == []
