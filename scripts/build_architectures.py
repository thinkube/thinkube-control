# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The architectures an application's images are built for.

Every render of templates/k8s/build-workflow.j2 takes its architectures from
here: the deploy's k8s/build-workflow.yaml, the WorkflowTemplate the deploy
applies, and the regeneration a commit of thinkube.yaml runs. They are the
architectures of the cluster's nodes, so an image runs on any node. A
cluster with a single architecture builds without per-architecture steps.
"""

from typing import Iterable, List, Mapping

ARCH_LABEL = "kubernetes.io/arch"


def cluster_architectures(node_labels: Iterable[Mapping[str, str]]) -> List[str]:
    """The distinct architectures of the nodes, sorted. Every node carries the label."""
    architectures = sorted({labels[ARCH_LABEL] for labels in node_labels})
    if not architectures:
        raise ValueError("The cluster reports no nodes, so no build architecture can be chosen")
    return architectures


def render_vars(architectures: List[str]) -> dict:
    """The build-workflow.j2 variables for these architectures."""
    return {"build_architectures": architectures} if len(architectures) > 1 else {}
