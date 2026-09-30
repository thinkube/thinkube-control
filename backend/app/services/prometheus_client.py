# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Prometheus client for querying metrics from kube-prometheus stack.

Prometheus is an optional component — it may or may not be installed.
This client probes availability on first use and caches the result.
All methods return None or empty results when Prometheus is unavailable.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://prometheus-k8s.monitoring.svc:9090"
PROBE_CACHE_TTL = 300  # Re-check availability every 5 minutes
QUERY_TIMEOUT = 5.0


class PrometheusQueryError(Exception):
    """A Prometheus query that must answer did not."""


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
    async def get_node_stats(cls) -> List[Dict[str, Any]]:
        """Get memory, CPU and GPU power for every node.

        Memory and CPU come from node-exporter, whose `instance` label is the
        node name. GPU power is the sum over the node's GPUs from DCGM, whose
        `Hostname` label is the node name. A value a node does not report is
        None: nodes without a GPU have no DCGM series, and the CPU rate needs
        two scrapes of a freshly started exporter.

        Raises PrometheusQueryError when any query fails.
        """
        queries = {
            "memory_total": 'node_memory_MemTotal_bytes{job="node-exporter"}',
            "memory_available": 'node_memory_MemAvailable_bytes{job="node-exporter"}',
            "cpu_percent": (
                '100 * (1 - avg by (instance) '
                '(rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[1m])))'
            ),
            "cpu_cores": 'count by (instance) (node_cpu_seconds_total{job="node-exporter",mode="idle"})',
            "gpu_power": "sum by (Hostname) (DCGM_FI_DEV_POWER_USAGE)",
        }
        results = await asyncio.gather(*(cls.query(q) for q in queries.values()))

        by_node: Dict[str, Dict[str, float]] = {}
        for (key, promql), result in zip(queries.items(), results):
            if result is None:
                raise PrometheusQueryError(f"Prometheus query failed: {promql}")
            label = "Hostname" if key == "gpu_power" else "instance"
            by_node[key] = {r["metric"][label]: float(r["value"][1]) for r in result}

        gib = 1024 ** 3
        nodes = []
        for name in sorted(by_node["memory_total"]):
            total = by_node["memory_total"][name]
            available = by_node["memory_available"].get(name)
            cpu = by_node["cpu_percent"].get(name)
            cores = by_node["cpu_cores"].get(name)
            power = by_node["gpu_power"].get(name)
            nodes.append({
                "name": name,
                "memory_total_gb": round(total / gib, 1),
                "memory_used_gb": round((total - available) / gib, 1) if available is not None else None,
                "cpu_percent": round(cpu, 1) if cpu is not None else None,
                "cpu_cores": int(cores) if cores is not None else None,
                "gpu_power_watts": round(power, 1) if power is not None else None,
            })
        return nodes

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
