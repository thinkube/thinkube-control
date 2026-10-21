#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A container's buildSize sets the memory its build pod gets.

The build workflow template gives every build the same 4Gi unless the
container declares a buildSize; the value is a workflow parameter, so one
Argo template serves every container.
"""

import json
import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

from thinkube_yaml_validator import validate_build_size, validate_test_size  # noqa: E402


def render(containers, architectures=None, deployment=None):
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
        thinkube_spec={"spec": {"containers": containers, **({"deployment": deployment} if deployment else {})}},
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
    """The API server takes only a literal quantity under container.resources,
    so the limit there stays 4Gi and podSpecPatch carries the parameter."""
    workflow = render([FRONTEND], architectures)
    build = templates(workflow)[template]
    assert {p["name"] for p in build["inputs"]["parameters"]} >= {"build_memory"}
    assert build["container"]["resources"]["limits"]["memory"] == "4Gi"
    patch = json.loads(build["podSpecPatch"].replace("{{inputs.parameters.build_tolerations}}", "[]"))
    [main] = patch["containers"]
    assert main["name"] == "main"
    assert main["resources"]["limits"]["memory"] == "{{inputs.parameters.build_memory}}"


def test_an_unknown_build_size_is_refused_with_the_allowed_values():
    config = {"spec": {"containers": [{**FRONTEND, "buildSize": "huge"}]}}
    [error] = validate_build_size(config)
    assert "buildSize 'huge'" in error
    assert "small, medium, large, xlarge" in error


def test_known_and_absent_build_sizes_pass():
    assert validate_build_size({"spec": {"containers": [BACKEND, FRONTEND]}}) == []


def _checkout(workflow):
    return next(v for v in workflow["spec"]["volumes"] if v["name"] == "shared-code")["hostPath"]["path"]


def test_the_control_plane_build_mounts_an_app_from_apps():
    assert _checkout(render([BACKEND])) == "/home/thinkube/shared-code/apps/notes"


def test_the_control_plane_build_mounts_a_component_from_components():
    workflow = render([BACKEND], deployment={"type": "component", "name": "notes"})
    assert _checkout(workflow) == "/home/thinkube/shared-code/components/notes"


# A container's test.size sets the memory and CPU its test pod gets.


def pipeline_test_tasks(workflow):
    """The test tasks of the pipeline, by name, with their parameters as a dict."""
    tasks = templates(workflow)["ci-cd-pipeline"]["dag"]["tasks"]
    return {
        t["name"]: {p["name"]: p["value"] for p in t["arguments"]["parameters"]}
        for t in tasks
        if t["name"].startswith("test-")
    }


def test_a_container_without_test_size_tests_in_1gi_and_one_cpu():
    tasks = pipeline_test_tasks(render([{**BACKEND, "test": {"enabled": True, "command": "./run_tests.sh"}}]))
    assert tasks["test-backend"]["test_memory"] == "1Gi"
    assert tasks["test-backend"]["test_cpu"] == "1"


@pytest.mark.parametrize(
    "size,memory,cpu",
    [("small", "1Gi", "1"), ("medium", "4Gi", "2"), ("large", "8Gi", "4"), ("xlarge", "16Gi", "8")],
)
def test_each_test_size_has_its_memory_and_cpu(size, memory, cpu):
    tasks = pipeline_test_tasks(render([{**BACKEND, "test": {"enabled": True, "command": "./run_tests.sh", "size": size}}]))
    assert (tasks["test-backend"]["test_memory"], tasks["test-backend"]["test_cpu"]) == (memory, cpu)


def test_the_test_pod_resources_are_the_test_size_parameters():
    run = templates(render([{**BACKEND, "test": {"enabled": True, "command": "./run_tests.sh", "size": "large"}}]))["run-test"]
    assert {p["name"] for p in run["inputs"]["parameters"]} >= {"test_memory", "test_cpu", "test_memory_request", "test_cpu_request"}
    assert "resources" not in run["container"], "the size reaches the pod through podSpecPatch only"
    patch = json.loads(run["podSpecPatch"])
    [main] = patch["containers"]
    assert main["resources"]["limits"] == {"memory": "{{inputs.parameters.test_memory}}", "cpu": "{{inputs.parameters.test_cpu}}"}


def test_the_test_pod_is_told_where_the_whole_checkout_is():
    run = templates(render([{**BACKEND, "test": {"enabled": True, "command": "./run_tests.sh"}}]))["run-test"]
    env = {e["name"]: e.get("value") for e in run["container"]["env"]}
    assert env["SOURCE_ROOT"] == "/workspace"
    assert {"name": "shared-code", "mountPath": "/workspace"} in run["container"]["volumeMounts"]


def test_an_unknown_test_size_is_refused_with_the_allowed_values():
    config = {"spec": {"containers": [{**BACKEND, "test": {"enabled": True, "size": "huge"}}]}}
    [error] = validate_test_size(config)
    assert "test.size 'huge'" in error
    assert "small, medium, large, xlarge" in error


def test_known_and_absent_test_sizes_pass():
    config = {"spec": {"containers": [{**BACKEND, "test": {"size": "large"}}, {**FRONTEND}, {**BACKEND, "test": None}]}}
    assert validate_test_size(config) == []


# ---- builds beside a training -------------------------------------------------

BIG = {"name": "big", "build": "./big", "port": 8000, "health": "/", "buildSize": "xlarge"}
TOLERATION = [{"key": "thinkube.io/training", "operator": "Exists", "effect": "NoSchedule"}]


def dag(workflow):
    tasks = templates(workflow)["ci-cd-pipeline"]["dag"]["tasks"]
    return {t["name"]: t for t in tasks}


def params(task):
    return {p["name"]: p["value"] for p in task["arguments"]["parameters"]}


@pytest.mark.parametrize("architectures,template", [
    (None, "buildah-build"), (["amd64", "arm64"], "buildah-build-on-arch"),
])
def test_a_build_pod_takes_its_tolerations_from_the_parameter_and_is_one_per_training_node(architectures, template):
    build = templates(render([BACKEND], architectures))[template]
    assert "build_tolerations" in {p["name"] for p in build["inputs"]["parameters"]}
    patch = json.loads(build["podSpecPatch"].replace("{{inputs.parameters.build_tolerations}}", json.dumps(TOLERATION)))
    assert patch["tolerations"] == TOLERATION
    assert build["metadata"]["labels"] == {"thinkube.io/build-pod": "true"}
    [term] = build["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert term == {"labelSelector": {"matchLabels": {"thinkube.io/build-pod": "true"}}, "topologyKey": "thinkube.io/training"}


def test_every_build_but_an_xlarge_one_tolerates_training_mode():
    tasks = dag(render([BACKEND, FRONTEND], ["amd64", "arm64"]))
    for name in ("build-backend-arm64", "build-frontend-arm64", "build-backend-amd64"):
        assert json.loads(params(tasks[name])["build_tolerations"]) == TOLERATION
        assert tasks[name]["dependencies"] == [f"test-{name.split('-')[1]}"]
    assert not [n for n in tasks if n.startswith("check-training-")]


def test_an_xlarge_build_does_not_tolerate_training_mode_and_is_checked_first():
    tasks = dag(render([BIG], ["amd64", "arm64"]))
    for arch in ("amd64", "arm64"):
        build = tasks[f"build-big-{arch}"]
        assert json.loads(params(build)["build_tolerations"]) == []
        assert build["dependencies"] == [f"check-training-big-{arch}"]
        check = tasks[f"check-training-big-{arch}"]
        assert check["template"] == "check-training-node"
        assert check["dependencies"] == ["test-big"]
        assert params(check)["node_selector"] == f"kubernetes.io/arch={arch}"
        assert params(check)["build_memory"] == "32Gi"


def test_a_single_architecture_xlarge_build_checks_the_control_plane():
    tasks = dag(render([BIG]))
    assert params(tasks["check-training-big"])["node_selector"] == "kubernetes.io/hostname=tkamd1"
    assert tasks["build-big"]["dependencies"] == ["check-training-big"]


def test_the_check_refuses_with_a_sentence_that_says_why():
    source = templates(render([BIG], ["arm64"]))["check-training-node"]["script"]["source"]
    assert "refused: its buildSize is xlarge" in source
    assert "does not run on the training node while training mode is on" in source
    assert "exit 1" in source


def test_the_image_builder_may_read_the_nodes():
    rules = [d for d in yaml.safe_load_all((REPO / "k8s/image-builder-setup.yaml").read_text())
             if d and d["kind"] == "ClusterRole"][0]["rules"]
    assert {"apiGroups": [""], "resources": ["nodes"], "verbs": ["get", "list"]} in rules
