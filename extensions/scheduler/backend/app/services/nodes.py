# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The nodes as the scheduler knows them: each GPU by its id, with its memory and what it holds now, and the
node's kind.

A node's kind says what the work on it shares: on a node with "separate" GPUs each GPU has memory of its own, and
two pieces of work share something only when they are on the same GPU; on a "shared" node the GPU takes its memory
from the system's (the DGX Spark), so all the work on the node shares one memory; a node of kind "none" has no GPU.

The figures come from Thinkube Control's node-metrics pods, one on each node, which read them from the node itself.
They show the work the scheduler did not start as well as its own.
"""

import asyncio
from typing import Any, Dict, List

import httpx

from app.core.config import settings

SEPARATE, SHARED, NONE = "separate", "shared", "none"
MIB = 2**20


class NodesError(Exception):
    """The nodes, or one node, could not be read; the message says why."""


def node_of(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """A node from what its node-metrics pod answers."""
    name = metrics["node_name"]
    if metrics.get("gpu_error"):
        raise NodesError(f"{name} could not read its GPUs: {metrics['gpu_error']}")
    found = metrics["gpus"]
    without = [g["index"] for g in found if not g.get("uuid")]
    if without:
        raise NodesError(f"the node-metrics pod of {name} gives no id for GPU {', '.join(map(str, without))}: it is "
                         "older than the scheduler needs; deploying Thinkube Control replaces it")
    kind = NONE if not found else SHARED if metrics["is_uma"] else SEPARATE
    gpus = []
    for g in sorted(found, key=lambda g: g["index"]):
        gpu = {"id": g["uuid"], "index": g["index"], "name": g["name"], "utilization": g["utilization"],
               "temperature": g["temp"], "power": g["power"]}
        if kind == SEPARATE:
            gpu.update(memory_total_bytes=int(g["memory_total_mb"] * MIB),
                       memory_used_bytes=int(g["memory_used_mb"] * MIB),
                       memory_free_bytes=int(g["memory_free_mb"] * MIB))
        gpus.append(gpu)
    return {"name": name, "kind": kind, "gpus": gpus,
            "memory_total_bytes": metrics["memory_total_bytes"],
            "memory_available_bytes": metrics["memory_available_bytes"]}


class Nodes:
    """The cluster's nodes, read from their node-metrics pods."""

    def _pods(self) -> Dict[str, str]:
        """The address of the node-metrics pod of each node."""
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        try:
            pods = client.CoreV1Api().list_namespaced_pod(settings.SCHEDULER_METRICS_NAMESPACE,
                                                          label_selector="app=node-metrics")
        except ApiException as e:
            raise NodesError(f"Kubernetes refused to list the node-metrics pods: {e.status} {e.reason}") from e
        return {pod.spec.node_name: pod.status.pod_ip for pod in pods.items
                if pod.status.phase == "Running" and pod.status.pod_ip and pod.spec.node_name}

    async def _node(self, http: httpx.AsyncClient, name: str, address: str) -> Dict[str, Any]:
        try:
            answer = await http.get(f"http://{address}:{settings.SCHEDULER_METRICS_PORT}/metrics")
            answer.raise_for_status()
            return node_of(answer.json())
        except httpx.HTTPError as e:
            return {"name": name, "error": f"the node-metrics pod of {name} did not answer: {e}"}
        except NodesError as e:
            return {"name": name, "error": str(e)}

    async def list(self) -> List[Dict[str, Any]]:
        """Every node that has a node-metrics pod, by name; a node that could not be read has its error in place of
        its figures."""
        pods = await asyncio.to_thread(self._pods)
        if not pods:
            raise NodesError(f"no node-metrics pod is running in {settings.SCHEDULER_METRICS_NAMESPACE}")
        async with httpx.AsyncClient(timeout=10.0) as http:
            found = await asyncio.gather(*(self._node(http, name, pods[name]) for name in sorted(pods)))
        return list(found)


nodes = Nodes()
