"""Cluster resources API for real-time resource availability"""

import asyncio
import json
import logging
import time
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from kubernetes import client, config
from kubernetes.stream import stream

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

# Hard bound on the nvidia-smi exec so a busy/unresponsive GPU node can never
# make this endpoint hang (the rest of the data is still returned without it).
_GPU_EXEC_TIMEOUT_SECONDS = 8


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
    for pod in pods_payload.get("items", []):
        node_of_pod = (pod.get("spec") or {}).get("nodeName")
        pods_by_node.setdefault(node_of_pod, []).append(pod)

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

        # Get GPU details if node has GPUs
        gpu_details = []
        if capacity["gpu"] > 0:
            try:
                gpu_details = _get_gpu_details(node_name, v1)
            except Exception as e:
                logger.warning(f"Could not get GPU details for {node_name}: {e}")
                # Create basic GPU info without nvidia-smi details
                for i in range(capacity["gpu"]):
                    gpu_details.append({
                        "index": i,
                        "model": "Unknown GPU",
                        "memory_total": "Unknown",
                        "memory_used": "Unknown",
                        "memory_free": "Unknown",
                        "available": i >= allocated_gpu
                    })

        # Calculate available resources
        available = {
            "cpu": max(0, capacity["cpu"] - allocated_cpu),
            "memory": max(0, capacity["memory"] - allocated_memory),
            "gpu": max(0, capacity["gpu"] - allocated_gpu)
        }

        # Effective GPU: for time-sliced nodes (virtual > physical),
        # cap to 1 since multiple partitions share the same memory
        # pool with no benefit (especially on unified-memory like DGX Spark)
        physical_gpus = len(gpu_details)
        effective_gpu = capacity["gpu"]
        if physical_gpus > 0 and capacity["gpu"] > physical_gpus:
            effective_gpu = 1

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
            "gpu_details": gpu_details
        })

    return result


def _get_gpu_details(node_name: str, v1: client.CoreV1Api) -> List[Dict[str, Any]]:
    """Get detailed GPU information from nvidia-smi via gpu-operator pod"""

    # Find nvidia driver pod on this node
    pods = v1.list_namespaced_pod(
        namespace="gpu-operator",
        field_selector=f"spec.nodeName={node_name}"
    )

    driver_pod = None
    for pod in pods.items:
        if "nvidia-driver" in pod.metadata.name and pod.status.phase == "Running":
            driver_pod = pod
            break

    if not driver_pod:
        raise Exception(f"No running nvidia-driver pod found on node {node_name}")

    # Execute nvidia-smi to get GPU details
    exec_command = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader"
    ]

    try:
        response = stream(
            v1.connect_get_namespaced_pod_exec,
            driver_pod.metadata.name,
            "gpu-operator",
            command=exec_command,
            stderr=False,
            stdin=False,
            stdout=True,
            tty=False,
            _request_timeout=_GPU_EXEC_TIMEOUT_SECONDS
        )

        gpus = []
        for line in response.strip().split('\n'):
            if line:
                parts = [p.strip() for p in line.split(',')]

                # Parse memory values
                memory_used = parts[3]
                memory_used_val = 0
                if ' MiB' in memory_used:
                    memory_used_val = int(memory_used.split(' MiB')[0])

                # Parse utilization
                utilization = 0
                if len(parts) > 5:
                    util_str = parts[5]
                    if ' %' in util_str:
                        utilization = int(util_str.split(' %')[0])

                gpus.append({
                    "index": int(parts[0]),
                    "model": parts[1],
                    "memory_total": parts[2],
                    "memory_used": parts[3],
                    "memory_free": parts[4],
                    "utilization": utilization,
                    "available": memory_used_val < 100 and utilization < 5
                })

        return gpus

    except Exception as e:
        logger.error(f"Failed to execute nvidia-smi on {node_name}: {e}")
        raise
