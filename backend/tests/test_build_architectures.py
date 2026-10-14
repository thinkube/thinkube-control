#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Every render of the build template builds for the cluster's architectures.

The deploy's k8s/build-workflow.yaml, the template the deploy applies, and the
one a commit regenerates take the same variables from
scripts/build_architectures.py.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_architectures import (  # noqa: E402
    app_architectures,
    cluster_architectures,
    declared_architectures,
    node_label_architecture,
    render_vars,
)
from test_build_workflow import BACKEND, build_tasks, render  # noqa: E402

ARCH = "kubernetes.io/arch"


def test_the_architectures_are_the_distinct_ones_of_the_nodes_sorted():
    nodes = [{ARCH: "arm64"}, {ARCH: "amd64"}, {ARCH: "amd64"}]
    assert cluster_architectures(nodes) == ["amd64", "arm64"]


def test_a_cluster_without_nodes_is_refused():
    with pytest.raises(ValueError, match="no nodes"):
        cluster_architectures([])


def test_the_control_plane_builds_its_own_single_architecture():
    assert render_vars(["amd64"], "amd64") == {}
    assert set(build_tasks(render([BACKEND]))) == {"build-backend"}


def test_a_mixed_cluster_builds_each_container_once_per_architecture():
    variables = render_vars(cluster_architectures([{ARCH: "amd64"}, {ARCH: "arm64"}]), "amd64")
    tasks = build_tasks(render([BACKEND], variables["build_architectures"]))
    assert {"build-backend-amd64", "build-backend-arm64"} <= set(tasks)


def test_without_a_declaration_the_app_builds_for_every_architecture_of_the_cluster():
    assert declared_architectures({}) is None
    assert app_architectures(["amd64", "arm64"], None) == ["amd64", "arm64"]


def test_a_declaration_keeps_the_cluster_architectures_it_names():
    declared = declared_architectures({"architectures": ["amd64"]})
    assert app_architectures(["amd64", "arm64"], declared) == ["amd64"]


def test_a_declaration_the_cluster_cannot_run_is_refused():
    with pytest.raises(ValueError, match="no node can run"):
        app_architectures(["arm64"], ["amd64"])


@pytest.mark.parametrize("value", [[], "amd64", ["x86_64"], ["amd64", "riscv64"]])
def test_a_declaration_outside_the_known_architectures_is_refused(value):
    with pytest.raises(ValueError, match="spec.architectures"):
        declared_architectures({"architectures": value})


def test_one_architecture_other_than_the_control_plane_builds_on_a_node_of_it():
    variables = render_vars(["arm64"], "amd64")
    assert variables == {"build_architectures": ["arm64"]}
    tasks = build_tasks(render([BACKEND], variables["build_architectures"]))
    assert "build-backend-arm64" in tasks and "build-backend" not in tasks


def test_the_control_plane_architecture_is_read_from_its_node():
    nodes = [("tkamd1", {ARCH: "amd64"}), ("tkspark", {ARCH: "arm64"})]
    assert node_label_architecture(nodes, "tkspark") == "arm64"
    with pytest.raises(ValueError, match="MASTER_NODE_NAME"):
        node_label_architecture(nodes, "absent")


def _deployment(spec_extra, container_extra=None):
    import yaml
    from test_platform_credentials import render as render_template

    container = {"name": "backend", "build": "./backend", "port": 8000, **(container_extra or {})}
    spec = {"spec": {"containers": [container], "deployment": {"type": "app", "replicas": 1}, **spec_extra}}
    text = render_template("deployment-separate.j2", thinkube_spec=spec)
    return next(d for d in yaml.safe_load_all(text) if d and d.get("kind") == "Deployment")


def test_declared_architectures_keep_the_pods_to_those_nodes():
    pod = _deployment({"architectures": ["amd64"]})["spec"]["template"]["spec"]
    term = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]
    assert term["matchExpressions"] == [{"key": ARCH, "operator": "In", "values": ["amd64"]}]


def test_an_app_without_the_declaration_has_no_architecture_affinity():
    assert "affinity" not in _deployment({})["spec"]["template"]["spec"]


def test_the_architecture_affinity_sits_beside_the_gpu_preference():
    pod = _deployment({"architectures": ["arm64"]}, {"gpu": {"count": 1, "memory": "20Gi"}})["spec"]["template"]["spec"]
    node_affinity = pod["affinity"]["nodeAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" in node_affinity
    assert "preferredDuringSchedulingIgnoredDuringExecution" in node_affinity
