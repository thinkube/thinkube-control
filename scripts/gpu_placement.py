# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The node of each app container that uses a GPU, chosen when the app's manifests are rendered.

An app's container declares its GPUs in thinkube.yaml (gpu: count, and memory, the GPU memory it takes on each).
The Scheduler gives them: Thinkube Control asks it for a hold for the container's Deployment, and writes the ids
of the GPUs the hold was given into the ConfigMap GPU_CONFIGMAP, which the pod reads. The pod asks Kubernetes for
no GPU, so it runs on one node, written into its manifest here: the node it runs on already when that node's GPUs
can hold it, else the node whose GPUs hold it with the most memory free.

A component's containers are not placed here: the LLM gateway starts their pods on GPUs it holds itself.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# The Scheduler's list of the nodes, their kind and GPUs, for the parts of the platform
SCHEDULER_NODES_URL = "http://scheduler-backend.thinkube-control.svc.cluster.local:8000/internal/scheduler/nodes"
GIB = 2**30
# The share of a GPU's memory the Scheduler keeps free
MARGIN = 0.05
# The annotations of a Deployment whose GPUs Thinkube Control asks the Scheduler for
GPUS_ANNOTATION = "scheduler.thinkube.io/gpus"
GPU_MEMORY_ANNOTATION = "scheduler.thinkube.io/gpu-memory-gb"
# The runtime class with which NVIDIA_VISIBLE_DEVICES takes a GPU's id
GPU_RUNTIME_CLASS = "nvidia-legacy"
# The ConfigMap, beside the Deployment, that holds the ids of its GPUs (key NVIDIA_VISIBLE_DEVICES)
GPU_CONFIGMAP = "{deployment}-gpus"


class PlacementError(Exception):
    """No node can run a container's GPUs, or the Scheduler could not be read; the message says which."""


def held_containers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The containers whose GPUs the Scheduler gives: those that declare gpu.count, in an app that is not a
    component."""
    spec = config.get("spec", {})
    if spec.get("deployment", {}).get("type") == "component":
        return []
    return [c for c in spec.get("containers", []) if (c.get("gpu") or {}).get("count")]


def memory_gb(container: Dict[str, Any]) -> int:
    """The GPU memory a container takes on each GPU, from gpu.memory ("20Gi")."""
    memory = str((container.get("gpu") or {}).get("memory", ""))
    found = re.fullmatch(r"([0-9]+)Gi", memory)
    if not found:
        raise PlacementError(f"container {container.get('name')}: gpu.memory is the GPU memory it takes on each GPU, "
                             f"written as <number>Gi, such as 20Gi, not {memory!r}")
    return int(found.group(1))


def _room(node: Dict[str, Any], count: int, memory: int) -> Optional[float]:
    """The memory free on the node's GPUs that would hold the container, or None when they cannot."""
    if node.get("error") or node.get("kind") not in ("separate", "shared"):
        return None
    gpus = node.get("gpus") or []
    if len(gpus) < count:
        return None
    if node["kind"] == "shared":
        if node.get("memory_total_bytes", 0) < memory * GIB:
            return None
        return float(node.get("memory_available_bytes", 0))
    able = sorted((g["memory_free_bytes"] for g in gpus if g["memory_total_bytes"] * (1 - MARGIN) >= memory * GIB),
                  reverse=True)
    if len(able) < count:
        return None
    return float(sum(able[:count]))


def choose(config: Dict[str, Any], app_name: str, current: Dict[str, str],
           nodes: List[Dict[str, Any]]) -> Dict[str, str]:
    """{container name: node} for each container whose GPUs the Scheduler gives. current: the node each one's
    Deployment runs on now, by container name."""
    by_name = {n["name"]: n for n in nodes}
    chosen = {}
    for container in held_containers(config):
        count, memory = int(container["gpu"]["count"]), memory_gb(container)
        here = current.get(container["name"])
        if here in by_name and _room(by_name[here], count, memory) is not None:
            chosen[container["name"]] = here
            continue
        fitting = sorted(((_room(n, count, memory), n["name"]) for n in nodes), reverse=True)
        fitting = [name for room, name in fitting if room is not None]
        if not fitting:
            raise PlacementError(
                f"{app_name}-{container['name']} needs {count} GPU(s) with {memory} GiB each, and no node has them: "
                + "; ".join(f"{n['name']}: " + (n.get("error") or ", ".join(
                    f"{g.get('name')} {g.get('memory_total_bytes', 0) / GIB:.0f} GiB" for g in n.get("gpus") or [])
                    or "no GPU") for n in nodes))
        chosen[container["name"]] = fitting[0]
    return chosen


def read_nodes(url: str = SCHEDULER_NODES_URL) -> List[Dict[str, Any]]:
    """The nodes as the Scheduler reads them."""
    try:
        with urllib.request.urlopen(url, timeout=30) as answer:
            return json.load(answer)["nodes"]
    except urllib.error.HTTPError as e:
        raise PlacementError(f"the Scheduler answered {e.code} for its nodes: {e.read().decode()[:300]}") from e
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise PlacementError(f"the Scheduler's nodes could not be read at {url}: {e}") from e


def node_of(deployment: Any) -> Optional[str]:
    """The node a Deployment (a Kubernetes client object) is pinned to, or None."""
    selector = (deployment.spec.template.spec.node_selector or {}) if deployment is not None else {}
    return selector.get("kubernetes.io/hostname")
