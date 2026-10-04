# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Per-node system metrics API endpoints.

Uses Prometheus (kube-prometheus node-exporter + DCGM exporter), an optional
component. Returns monitoring_available=false when it is not installed.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import time
from datetime import datetime
from app.core.api_tokens import get_current_user_dual_auth
from app.services.prometheus_client import PrometheusClient, PrometheusQueryError

router = APIRouter()

# Server-side cache
_metrics_cache: Dict[str, Any] = {}
_metrics_cache_time: float = 0
_METRICS_CACHE_TTL: float = 2.0


@router.get("/gpu/metrics")
async def get_gpu_metrics(
    current_user: dict = Depends(get_current_user_dual_auth),
) -> Dict[str, Any]:
    """Get memory, CPU and GPU power for every node.

    Each entry in `nodes` has name, memory_used_gb, memory_total_gb,
    cpu_percent, cpu_cores and gpu_power_watts (summed over the node's GPUs).
    A value the node does not report is null. Returns monitoring_available=false
    when Prometheus is not installed.
    """
    global _metrics_cache, _metrics_cache_time

    now = time.monotonic()
    if _metrics_cache and (now - _metrics_cache_time) < _METRICS_CACHE_TTL:
        return _metrics_cache

    if not PrometheusClient.is_available():
        result = {
            "monitoring_available": False,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    else:
        try:
            nodes = await PrometheusClient.get_node_stats()
        except PrometheusQueryError as e:
            raise HTTPException(status_code=502, detail=str(e))
        result = {
            "monitoring_available": True,
            "nodes": nodes,
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
