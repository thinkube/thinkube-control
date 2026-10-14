# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The architectures an application's images are built for and run on.

Every render of templates/k8s/build-workflow.j2 takes its architectures from
here: the deploy's k8s/build-workflow.yaml, the WorkflowTemplate the deploy
applies, and the regeneration a commit of thinkube.yaml runs. They are the
architectures of the cluster's nodes, so an image runs on any node, unless
thinkube.yaml declares spec.architectures: then they are the declared ones
the cluster has, and the application's pods are kept to nodes of those
architectures (an upstream that publishes packages for one architecture
only, for instance).

The build runs on the control plane when the one architecture to build is
the control plane's own; otherwise each architecture builds on a node of
that architecture.
"""

from typing import Iterable, List, Mapping, Optional

ARCH_LABEL = "kubernetes.io/arch"
KNOWN_ARCHITECTURES = ("amd64", "arm64")


def cluster_architectures(node_labels: Iterable[Mapping[str, str]]) -> List[str]:
    """The distinct architectures of the nodes, sorted. Every node carries the label."""
    architectures = sorted({labels[ARCH_LABEL] for labels in node_labels})
    if not architectures:
        raise ValueError("The cluster reports no nodes, so no build architecture can be chosen")
    return architectures


def declared_architectures(spec: Mapping) -> Optional[List[str]]:
    """thinkube.yaml's spec.architectures, or None when it does not declare them."""
    declared = spec.get("architectures")
    if declared is None:
        return None
    if (not isinstance(declared, list) or not declared
            or any(a not in KNOWN_ARCHITECTURES for a in declared)):
        raise ValueError(
            f"spec.architectures is {declared!r}; it is a list of one or more of "
            f"{', '.join(KNOWN_ARCHITECTURES)}"
        )
    return declared


def app_architectures(cluster: List[str], declared: Optional[List[str]]) -> List[str]:
    """The architectures to build for: the cluster's, or those of them the application declares."""
    if declared is None:
        return cluster
    chosen = [a for a in cluster if a in declared]
    if not chosen:
        raise ValueError(
            f"thinkube.yaml declares spec.architectures {declared}, and the cluster's nodes are "
            f"{', '.join(cluster)}: no node can run this application"
        )
    return chosen


def render_vars(architectures: List[str], control_plane_architecture: str) -> dict:
    """The build-workflow.j2 variables for these architectures."""
    if architectures == [control_plane_architecture]:
        return {}
    return {"build_architectures": architectures}


def node_label_architecture(node_labels: Iterable[tuple], node_name: str) -> str:
    """The architecture of node_name, from (name, labels) pairs."""
    for name, labels in node_labels:
        if name == node_name:
            return labels[ARCH_LABEL]
    raise ValueError(f"Node {node_name} (MASTER_NODE_NAME) is not among the cluster's nodes")
