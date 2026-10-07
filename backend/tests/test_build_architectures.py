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

from build_architectures import cluster_architectures, render_vars  # noqa: E402
from test_build_workflow import BACKEND, build_tasks, render  # noqa: E402

ARCH = "kubernetes.io/arch"


def test_the_architectures_are_the_distinct_ones_of_the_nodes_sorted():
    nodes = [{ARCH: "arm64"}, {ARCH: "amd64"}, {ARCH: "amd64"}]
    assert cluster_architectures(nodes) == ["amd64", "arm64"]


def test_a_cluster_without_nodes_is_refused():
    with pytest.raises(ValueError, match="no nodes"):
        cluster_architectures([])


def test_a_single_architecture_renders_without_per_architecture_steps():
    assert render_vars(["amd64"]) == {}
    assert set(build_tasks(render([BACKEND]))) == {"build-backend"}


def test_a_mixed_cluster_builds_each_container_once_per_architecture():
    variables = render_vars(cluster_architectures([{ARCH: "amd64"}, {ARCH: "arm64"}]))
    tasks = build_tasks(render([BACKEND], variables["build_architectures"]))
    assert {"build-backend-amd64", "build-backend-arm64"} <= set(tasks)
