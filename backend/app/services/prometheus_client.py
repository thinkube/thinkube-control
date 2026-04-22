"""Prometheus client for querying metrics from kube-prometheus stack.

Prometheus is an optional component — it may or may not be installed.
This client probes availability on first use and caches the result.
All methods return None or empty results when Prometheus is unavailable.
"""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://prometheus-k8s.monitoring.svc:9090"
PROBE_CACHE_TTL = 300  # Re-check availability every 5 minutes
QUERY_TIMEOUT = 5.0


class PrometheusClient:
    """Client for Prometheus HTTP API with availability detection."""

    _available: Optional[bool] = None
    _last_probe: float = 0

    @classmethod
    def is_available(cls) -> bool:
        """Check if Prometheus is reachable. Result is cached for PROBE_CACHE_TTL seconds."""
        now = time.monotonic()
        if cls._available is not None and (now - cls._last_probe) < PROBE_CACHE_TTL:
            return cls._available

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{PROMETHEUS_URL}/api/v1/status/buildinfo")
                cls._available = resp.status_code == 200
        except Exception:
            cls._available = False

        cls._last_probe = now
        logger.info(f"Prometheus availability: {cls._available}")
        return cls._available

    @classmethod
    def invalidate_cache(cls):
        """Force re-probe on next call."""
        cls._available = None
        cls._last_probe = 0

    @classmethod
    async def query(cls, promql: str) -> Optional[List[Dict[str, Any]]]:
        """Execute an instant PromQL query.

        Returns list of result vectors, or None if Prometheus is unavailable.
        """
        if not cls.is_available():
            return None

        try:
            async with httpx.AsyncClient(timeout=QUERY_TIMEOUT) as client:
                resp = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": promql},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "success":
                    return data["data"]["result"]
                logger.warning(f"Prometheus query failed: {data.get('error')}")
                return None
        except Exception as e:
            logger.warning(f"Prometheus query error: {e}")
            return None

    @classmethod
    async def get_gpu_utilization(cls) -> Optional[Dict[str, Any]]:
        """Get GPU utilization, temperature, power from DCGM metrics.

        Returns dict with gpu_utilization, gpu_temp, memory_temp, power_usage,
        sm_clock, memory_bandwidth — or None if unavailable.
        """
        results = await cls.query(
            '{__name__=~"DCGM_FI_DEV_GPU_UTIL|DCGM_FI_DEV_GPU_TEMP|DCGM_FI_DEV_MEMORY_TEMP'
            '|DCGM_FI_DEV_POWER_USAGE|DCGM_FI_DEV_SM_CLOCK|DCGM_FI_DEV_MEM_COPY_UTIL"}'
        )
        if results is None:
            return None

        metrics: Dict[str, float] = {}
        for r in results:
            name = r["metric"]["__name__"]
            # Take first GPU instance (gpu="0") for single-GPU display
            if name not in metrics:
                metrics[name] = float(r["value"][1])

        if not metrics:
            return None

        return {
            "gpu_utilization": metrics.get("DCGM_FI_DEV_GPU_UTIL", 0),
            "gpu_temp": metrics.get("DCGM_FI_DEV_GPU_TEMP", 0),
            "memory_temp": metrics.get("DCGM_FI_DEV_MEMORY_TEMP", 0),
            "power_usage": metrics.get("DCGM_FI_DEV_POWER_USAGE", 0),
            "sm_clock": metrics.get("DCGM_FI_DEV_SM_CLOCK", 0),
            "memory_bandwidth": metrics.get("DCGM_FI_DEV_MEM_COPY_UTIL", 0),
        }

    @classmethod
    async def get_gpu_capacity(cls) -> Optional[Dict[str, Any]]:
        """Get total and allocatable GPU count from kube-state-metrics."""
        results = await cls.query(
            'kube_node_status_capacity{resource="nvidia_com_gpu"}'
        )
        if results is None:
            return None

        total = sum(int(float(r["value"][1])) for r in results)

        alloc_results = await cls.query(
            'kube_node_status_allocatable{resource="nvidia_com_gpu"}'
        )
        allocatable = sum(int(float(r["value"][1])) for r in (alloc_results or []))

        return {"total_gpus": total, "allocatable_gpus": allocatable}

    @classmethod
    async def get_gpu_usage_by_namespace(cls) -> Optional[Dict[str, Dict[str, Any]]]:
        """Get per-namespace GPU allocation from kube-state-metrics.

        Returns dict mapping namespace -> {"total_gpus": int, "gpu_nodes": list}
        or None if Prometheus is unavailable.
        """
        results = await cls.query(
            'kube_pod_container_resource_limits{resource="nvidia_com_gpu"}'
            ' * on(namespace, pod) group_left()'
            ' (kube_pod_status_phase{phase=~"Running|Pending"} == 1)'
        )
        if results is None:
            return None

        gpu_by_ns: Dict[str, Dict[str, Any]] = {}
        for r in results:
            ns = r["metric"].get("namespace", "")
            node = r["metric"].get("node", "")
            gpu_count = int(float(r["value"][1]))

            if ns not in gpu_by_ns:
                gpu_by_ns[ns] = {"total_gpus": 0, "gpu_nodes": set()}
            gpu_by_ns[ns]["total_gpus"] += gpu_count
            if node:
                gpu_by_ns[ns]["gpu_nodes"].add(node)

        # Convert sets to lists
        for info in gpu_by_ns.values():
            info["gpu_nodes"] = list(info["gpu_nodes"])

        return gpu_by_ns
