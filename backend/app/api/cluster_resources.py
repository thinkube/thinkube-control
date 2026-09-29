# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Cluster resources API for real-time resource availability"""

import asyncio
import json
import logging
import time
from typing import List, Dict, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from kubernetes import client, config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cluster", tags=["cluster-resources"])

# Short-lived cache so the endpoint returns reliably fast.
#
# Computing cluster resources requires listing every pod cluster-wide, which
# takes a few seconds on a busy cluster. JupyterHub calls this endpoint twice
# in quick succession per spawn (spawn-form render + spawn-time profile
# re-evaluation) with a 10s client timeout. Recomputing on every call left the
# endpoint flirting with that timeout; a timeout on the second call used to
# crash the hub. Caching makes the second call instant and bounds load when
# several users spawn at once. The lock coalesces concurrent cold-cache callers
# onto a single computation instead of stampeding the Kubernetes API server.
# A single cluster-wide pod list takes several seconds on a busy cluster, so we
# never compute in the request path. A background task (started from the app
# lifespan) keeps the cache warm; the endpoint returns the cached snapshot
# instantly. _CACHE_TTL_SECONDS is the staleness threshold at which a *read*
# triggers a background refresh, so the cache self-heals even if the loop isn't
# running (e.g. in tests). _REFRESH_INTERVAL_SECONDS is the proactive cadence.
# Cluster resource availability changes slowly, and each refresh deserialises
# the whole pod list (CPU/GIL-bound). Refresh infrequently so the periodic
# refresh doesn't repeatedly stall the event loop and starve latency-critical
# in-memory endpoints like /llm/models/resolve under load.
_CACHE_TTL_SECONDS = 120
_REFRESH_INTERVAL_SECONDS = 60
_cache: Dict[str, Any] = {"data": None, "updated_at": 0.0}
_cache_lock = asyncio.Lock()
_refreshing = False

# GPU details come from the node-metrics DaemonSet, which runs nvidia-smi on
# each host. Each node's own pod is asked by IP: the Service would answer from
# a random node. The timeout keeps a busy GPU node from stalling the refresh.
NODE_METRICS_NAMESPACE = "thinkube-control"
NODE_METRICS_PORT = 9100
_NODE_METRICS_TIMEOUT_SECONDS = 8


class GPUDetailsError(Exception):
    """A GPU node's details could not be read."""


async def _refresh_once() -> List[Dict[str, Any]]:
    """Recompute cluster resources off the event loop and update the cache."""
    data = await asyncio.to_thread(_compute_cluster_resources)
    _cache["data"] = data
    _cache["updated_at"] = time.monotonic()
    return data


async def _safe_refresh() -> None:
    """Background-safe refresh: swallows errors and de-dupes concurrent runs."""
    global _refreshing
    if _refreshing:
        return
    _refreshing = True
    try:
        await _refresh_once()
    except Exception as e:
        logger.warning(f"Background cluster-resources refresh failed: {e}")
    finally:
        _refreshing = False


async def refresh_cluster_resources_loop() -> None:
    """Proactively keep the cluster-resources cache warm.

    Started from the FastAPI lifespan so the endpoint never pays the multi-second
    pod-list cost in the request path (which used to exceed JupyterHub's spawn
    timeout and crash the hub).
    """
    while True:
        await _safe_refresh()
        await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)


def parse_memory(memory_str: str) -> int:
    """Parse Kubernetes memory string to bytes"""
    if memory_str.endswith('Ki'):
        return int(memory_str[:-2]) * 1024
    elif memory_str.endswith('Mi'):
        return int(memory_str[:-2]) * 1024 * 1024
    elif memory_str.endswith('Gi'):
        return int(memory_str[:-2]) * 1024 * 1024 * 1024
    elif memory_str.endswith('M'):
        return int(memory_str[:-1]) * 1024 * 1024
    elif memory_str.endswith('G'):
        return int(memory_str[:-1]) * 1024 * 1024 * 1024
    elif memory_str.endswith('K'):
        return int(memory_str[:-1]) * 1024
    return int(memory_str)


def format_memory(bytes_val: int) -> str:
    """Format bytes to human readable string"""
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val // (1024 * 1024 * 1024)}Gi"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val // (1024 * 1024)}Mi"
    elif bytes_val >= 1024:
        return f"{bytes_val // 1024}Ki"
    return str(bytes_val)


@router.get("/resources", response_model=List[Dict[str, Any]])
async def get_cluster_resources():
    """Get real-time cluster resource availability including GPU details.

    Served instantly from a cache kept warm by ``refresh_cluster_resources_loop``.
    Computing is never done synchronously in the request path; a stale read just
    kicks off a background refresh and returns the current snapshot.
    """
    data = _cache["data"]
    if data is not None:
        if time.monotonic() - _cache["updated_at"] > _CACHE_TTL_SECONDS:
            # Stale and (likely) no loop running — refresh in the background but
            # serve the current data immediately. Never block the response.
            asyncio.create_task(_safe_refresh())
        return data

    # Cold start: cache not warmed yet. Compute once, coalescing concurrent callers.
    async with _cache_lock:
        if _cache["data"] is not None:
            return _cache["data"]
        try:
            return await _refresh_once()
        except Exception as e:
            logger.error(f"Failed to get cluster resources: {e}")
            raise HTTPException(status_code=500, detail=str(e))


def _compute_cluster_resources() -> List[Dict[str, Any]]:
    """Synchronous cluster-resource computation.

    Runs in a worker thread (see :func:`get_cluster_resources`) so the blocking
    Kubernetes client calls never block the FastAPI event loop.
    """
    # Load kubernetes config
    try:
        config.load_incluster_config()
    except Exception:
        # Fallback for local development
        config.load_kube_config()

    v1 = client.CoreV1Api()

    # Get all nodes
    nodes = v1.list_node()

    # Fetch all pods ONCE and bucket by node.
    #
    # Two cost controls keep this from stalling the event loop (it runs every
    # refresh and the deserialisation is CPU/GIL-bound even in a worker thread):
    #   1. A single cluster-wide list (a per-node spec.nodeName field_selector is
    #      not index-backed — the API server re-lists every pod per node).
    #   2. `_preload_content=False` + manual JSON parse, so we DON'T construct a
    #      typed V1Pod object graph for every pod (the dominant GIL cost); we read
    #      only the few fields we need from plain dicts. A server-side phase
    #      filter also drops terminated pods from the payload.
    raw_pods = v1.list_pod_for_all_namespaces(
        _preload_content=False,
        field_selector="status.phase!=Succeeded,status.phase!=Failed",
    )
    pods_payload = json.loads(raw_pods.data)
    pods_by_node: Dict[str, List[dict]] = {}
    node_metrics_ips: Dict[str, str] = {}
    for pod in pods_payload.get("items", []):
        node_of_pod = (pod.get("spec") or {}).get("nodeName")
        pods_by_node.setdefault(node_of_pod, []).append(pod)
        metadata = pod.get("metadata") or {}
        status = pod.get("status") or {}
        if (
            metadata.get("namespace") == NODE_METRICS_NAMESPACE
            and (metadata.get("labels") or {}).get("app") == "node-metrics"
            and status.get("phase") == "Running"
            and status.get("podIP")
        ):
            node_metrics_ips[node_of_pod] = status["podIP"]

    result = []
    for node in nodes.items:
        node_name = node.metadata.name

        # Get node capacity
        cpu_capacity = node.status.capacity.get("cpu", "0")
        # Parse CPU - might be int or string with 'm' suffix
        if isinstance(cpu_capacity, str):
            if cpu_capacity.endswith('m'):
                cpu_val = int(cpu_capacity[:-1]) / 1000
            else:
                cpu_val = int(cpu_capacity)
        else:
            cpu_val = int(cpu_capacity)

        capacity = {
            "cpu": cpu_val,
            "memory": parse_memory(node.status.capacity.get("memory", "0")),
            "gpu": int(node.status.capacity.get("nvidia.com/gpu", 0))
        }

        # Calculate allocated resources from pods (pre-grouped per node)
        pods = pods_by_node.get(node_name, [])

        allocated_cpu = 0
        allocated_memory = 0
        allocated_gpu = 0

        # Terminated pods (Succeeded/Failed) are already excluded server-side via
        # the field_selector above. Each pod is a plain dict (raw JSON).
        for pod in pods:
            spec = pod.get("spec") or {}
            for container in spec.get("containers", []):
                limits = (container.get("resources") or {}).get("limits") or {}
                if not limits:
                    continue

                # CPU
                cpu_limit = limits.get("cpu", "0")
                if cpu_limit != "0":
                    if cpu_limit.endswith('m'):
                        allocated_cpu += int(cpu_limit[:-1]) / 1000
                    else:
                        try:
                            allocated_cpu += float(cpu_limit)
                        except ValueError:
                            # Skip invalid CPU values like "512M" (probably memory)
                            pass

                # Memory
                mem_limit = limits.get("memory", "0")
                if mem_limit != "0":
                    allocated_memory += parse_memory(mem_limit)

                # GPU
                gpu_limit = limits.get("nvidia.com/gpu", "0")
                if gpu_limit != "0":
                    allocated_gpu += int(gpu_limit)

        # One entry per physical GPU. When they cannot be read, the list is
        # empty and gpu_error says why.
        gpu_details: List[Dict[str, Any]] = []
        gpu_error: Optional[str] = None
        if capacity["gpu"] > 0:
            try:
                gpu_details = _get_gpu_details(node_name, node_metrics_ips.get(node_name))
            except GPUDetailsError as e:
                gpu_error = str(e)
                logger.warning(f"Could not get GPU details for {node_name}: {e}")

        # Calculate available resources
        available = {
            "cpu": max(0, capacity["cpu"] - allocated_cpu),
            "memory": max(0, capacity["memory"] - allocated_memory),
            "gpu": max(0, capacity["gpu"] - allocated_gpu)
        }

        # Effective GPU: a time-sliced node advertises several slices per
        # physical GPU (nvidia.com/gpu.replicas, set by GPU feature discovery).
        # A pod asking for more than one slice may get slices of the same GPU,
        # so a server on such a node is given at most one.
        labels = node.metadata.labels or {}
        replicas = int(labels.get("nvidia.com/gpu.replicas", "1"))
        effective_gpu = 1 if capacity["gpu"] > 0 and replicas > 1 else capacity["gpu"]

        result.append({
            "name": node_name,
            "capacity": {
                "cpu": capacity["cpu"],
                "memory": format_memory(capacity["memory"]),
                "gpu": capacity["gpu"],
                "effective_gpu": effective_gpu
            },
            "allocated": {
                "cpu": round(allocated_cpu, 2),
                "memory": format_memory(allocated_memory),
                "gpu": allocated_gpu
            },
            "available": {
                "cpu": round(available["cpu"], 2),
                "memory": format_memory(available["memory"]),
                "gpu": available["gpu"]
            },
            "gpu_details": gpu_details,
            "gpu_error": gpu_error,
        })

    return result


def _get_gpu_details(node_name: str, pod_ip: Optional[str]) -> List[Dict[str, Any]]:
    """The node's physical GPUs, as its node-metrics pod reads them with nvidia-smi.

    Memory is in MiB. A unified-memory GPU (DGX Spark GB10) has no memory of
    its own, so its memory fields read "unified".
    """
    if not pod_ip:
        raise GPUDetailsError(f"no running node-metrics pod on {node_name}")
    try:
        response = httpx.get(
            f"http://{pod_ip}:{NODE_METRICS_PORT}/metrics",
            timeout=_NODE_METRICS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        metrics = response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise GPUDetailsError(f"node-metrics on {node_name} ({pod_ip}) did not answer: {e}") from e

    if metrics.get("gpu_error"):
        raise GPUDetailsError(f"nvidia-smi failed on {node_name}: {metrics['gpu_error']}")
    if not metrics.get("gpus"):
        raise GPUDetailsError(f"nvidia-smi found no GPU on {node_name}")

    def mib(value: float) -> str:
        return "unified" if metrics.get("is_uma") else f"{int(value)} MiB"

    gpus = []
    for gpu in metrics["gpus"]:
        utilization = int(gpu["utilization"])
        gpus.append({
            "index": gpu["index"],
            "model": gpu["name"],
            "memory_total": mib(gpu["memory_total_mb"]),
            "memory_used": mib(gpu["memory_used_mb"]),
            "memory_free": mib(gpu["memory_free_mb"]),
            "utilization": utilization,
            "available": gpu["memory_used_mb"] < 100 and utilization < 5,
        })
    return gpus
