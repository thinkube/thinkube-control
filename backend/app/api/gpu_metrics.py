"""
GPU and system metrics API endpoints.

Uses Prometheus (kube-prometheus + DCGM exporter) when available.
Falls back to node-metrics DaemonSet for system memory/CPU.
Returns {"available": false} when monitoring is not installed.
"""
from fastapi import APIRouter, Depends
from typing import Dict, Any
import httpx
import time
import logging
from datetime import datetime
from app.core.api_tokens import get_current_user_dual_auth
from app.services.prometheus_client import PrometheusClient

router = APIRouter()
logger = logging.getLogger(__name__)

NODE_METRICS_URL = "http://node-metrics.thinkube-control.svc.cluster.local:9100/metrics"
NODE_METRICS_TIMEOUT = 5.0

# Server-side cache
_metrics_cache: Dict[str, Any] = {}
_metrics_cache_time: float = 0
_METRICS_CACHE_TTL: float = 2.0


async def fetch_node_metrics() -> Dict[str, Any]:
    """Fetch system memory, CPU, and GPU allocatable from node-metrics DaemonSet."""
    try:
        async with httpx.AsyncClient(timeout=NODE_METRICS_TIMEOUT) as client:
            response = await client.get(NODE_METRICS_URL)
            response.raise_for_status()
            data = response.json()
            return {
                "memory_bytes": data["memory_used_bytes"],
                "memory_total_bytes": data["memory_total_bytes"],
                "memory_available_bytes": data.get("memory_available_bytes", 0),
                "swap_total_bytes": data.get("swap_total_bytes", 0),
                "swap_free_bytes": data.get("swap_free_bytes", 0),
                "cpu_percent": data.get("cpu_percent", 0),
                "is_uma": data.get("is_uma", False),
                "gpu_allocatable_bytes": data.get("gpu_allocatable_bytes", 0),
                "gpu_utilization": data.get("gpu_utilization", 0),
                "gpu_temp": data.get("gpu_temp", 0),
                "gpu_power": data.get("gpu_power", 0),
            }
    except Exception as e:
        logger.warning(f"node-metrics unavailable: {e}")
        return {}


@router.get("/gpu/metrics")
async def get_gpu_metrics(
    current_user: dict = Depends(get_current_user_dual_auth),
) -> Dict[str, Any]:
    """Get current GPU and system metrics.

    Returns monitoring_available=false when Prometheus is not installed.
    GPU metrics come from Prometheus DCGM, system metrics from node-metrics.
    """
    global _metrics_cache, _metrics_cache_time

    now = time.monotonic()
    if _metrics_cache and (now - _metrics_cache_time) < _METRICS_CACHE_TTL:
        return _metrics_cache

    # Check Prometheus availability
    prom_available = PrometheusClient.is_available()

    if not prom_available:
        result = {
            "monitoring_available": False,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        _metrics_cache = result
        _metrics_cache_time = now
        return result

    # Fetch GPU metrics from Prometheus DCGM
    gpu_data = await PrometheusClient.get_gpu_utilization()

    # Fetch system memory/CPU from node-metrics DaemonSet
    node = await fetch_node_metrics()

    # System memory
    system_memory_total_gb = 0.0
    system_memory_used_gb = 0.0
    system_memory_percent = 0.0
    cpu_percent = 0.0

    gpu_allocatable_gb = 0.0
    is_uma = False

    if node:
        total = node.get("memory_total_bytes", 0)
        used = node.get("memory_bytes", 0)
        if total > 0:
            system_memory_total_gb = total / (1024 ** 3)
            system_memory_used_gb = used / (1024 ** 3)
            system_memory_percent = (system_memory_used_gb / system_memory_total_gb) * 100
        cpu_percent = node.get("cpu_percent", 0.0)
        is_uma = node.get("is_uma", False)
        gpu_allocatable_gb = node.get("gpu_allocatable_bytes", 0) / (1024 ** 3)

    # GPU capacity from kube-state-metrics
    gpu_capacity = await PrometheusClient.get_gpu_capacity()

    result = {
        "monitoring_available": True,
        # GPU metrics from DCGM via Prometheus or node-metrics
        "gpu_utilization": gpu_data.get("gpu_utilization", 0) if gpu_data else node.get("gpu_utilization", 0),
        "memory_bandwidth": gpu_data.get("memory_bandwidth", 0) if gpu_data else 0,
        "gpu_temp": gpu_data.get("gpu_temp", 0) if gpu_data else node.get("gpu_temp", 0),
        "memory_temp": gpu_data.get("memory_temp", 0) if gpu_data else 0,
        "power_usage": gpu_data.get("power_usage", 0) if gpu_data else node.get("gpu_power", 0),
        "sm_clock": gpu_data.get("sm_clock", 0) if gpu_data else 0,
        # GPU capacity from kube-state-metrics
        "total_gpus": gpu_capacity.get("total_gpus", 0) if gpu_capacity else 0,
        "allocatable_gpus": gpu_capacity.get("allocatable_gpus", 0) if gpu_capacity else 0,
        # System metrics from node-metrics
        "system_memory_used_gb": round(system_memory_used_gb, 2),
        "system_memory_total_gb": round(system_memory_total_gb, 1),
        "system_memory_percent": round(system_memory_percent, 1),
        "cpu_percent": round(cpu_percent, 1),
        # GPU memory (real data from node-metrics)
        "gpu_allocatable_gb": round(gpu_allocatable_gb, 1),
        "unified_memory": is_uma,
        # Metadata
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    _metrics_cache = result
    _metrics_cache_time = now
    return result


@router.get("/gpu/monitoring-status")
async def get_monitoring_status(
    current_user: dict = Depends(get_current_user_dual_auth),
) -> Dict[str, Any]:
    """Check if Prometheus monitoring is available.

    Lightweight endpoint for frontend to decide whether to show monitoring UI.
    """
    return {
        "available": PrometheusClient.is_available(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
