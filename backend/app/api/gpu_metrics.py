"""
GPU and system metrics API endpoints
Fetches metrics directly from NVIDIA DCGM Exporter and Kubernetes metrics-server
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
import httpx
import re
from datetime import datetime
from ..core.security import get_current_user_dual_auth

router = APIRouter()

DCGM_EXPORTER_URL = "http://nvidia-dcgm-exporter.gpu-operator.svc.cluster.local:9400/metrics"
METRICS_SERVER_TIMEOUT = 5.0


def parse_prometheus_metrics(text: str) -> Dict[str, float]:
    """Parse Prometheus text format into a dictionary of metrics"""
    metrics = {}

    for line in text.split('\n'):
        # Skip comments and empty lines
        if line.startswith('#') or not line.strip():
            continue

        # Parse metric line: metric_name{labels} value
        match = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)\{.*?\}\s+([0-9.eE+-]+)', line)
        if match:
            metric_name = match.group(1)
            value = float(match.group(2))

            # Store first occurrence of each metric (single GPU system)
            if metric_name not in metrics:
                metrics[metric_name] = value

    return metrics


async def fetch_dcgm_metrics() -> Dict[str, float]:
    """Fetch GPU metrics from DCGM exporter"""
    try:
        async with httpx.AsyncClient(timeout=METRICS_SERVER_TIMEOUT) as client:
            response = await client.get(DCGM_EXPORTER_URL)
            response.raise_for_status()
            return parse_prometheus_metrics(response.text)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch GPU metrics: {str(e)}"
        )


async def fetch_node_metrics() -> Dict[str, Any]:
    """Fetch node metrics from Kubernetes metrics-server API"""
    try:
        # Use Kubernetes Python client to get node metrics
        from kubernetes import client, config

        # Load in-cluster config
        config.load_incluster_config()

        # Create custom objects API client
        api = client.CustomObjectsApi()

        # Get node metrics
        node_metrics = api.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes"
        )

        # Find tkspark node
        for item in node_metrics.get('items', []):
            if item['metadata']['name'] == 'tkspark':
                usage = item['usage']

                # Parse memory (e.g., "21782Mi" -> bytes)
                memory_str = usage['memory']
                if memory_str.endswith('Ki'):
                    memory_bytes = int(memory_str[:-2]) * 1024
                elif memory_str.endswith('Mi'):
                    memory_bytes = int(memory_str[:-2]) * 1024 * 1024
                elif memory_str.endswith('Gi'):
                    memory_bytes = int(memory_str[:-2]) * 1024 * 1024 * 1024
                else:
                    memory_bytes = int(memory_str)

                # Parse CPU (e.g., "1297m" -> millicores)
                cpu_str = usage['cpu']
                if cpu_str.endswith('m'):
                    cpu_millicores = int(cpu_str[:-1])
                elif cpu_str.endswith('n'):
                    cpu_millicores = int(cpu_str[:-1]) // 1000000
                else:
                    cpu_millicores = int(float(cpu_str) * 1000)

                return {
                    'memory_bytes': memory_bytes,
                    'cpu_millicores': cpu_millicores
                }

        raise HTTPException(status_code=404, detail="tkspark node not found")

    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch node metrics: {str(e)}"
        )


@router.get("/gpu/metrics")
async def get_gpu_metrics(
    current_user: dict = Depends(get_current_user_dual_auth)
) -> Dict[str, Any]:
    """
    Get current GPU and system metrics

    Returns:
        - gpu_utilization: GPU compute utilization percentage (0-100)
        - memory_bandwidth: Memory bandwidth utilization percentage (0-100)
        - gpu_temp: GPU temperature in Celsius
        - memory_temp: Memory temperature in Celsius
        - power_usage: Current power draw in watts
        - sm_clock: SM clock frequency in MHz
        - system_memory_used_gb: System memory used in GB
        - system_memory_total_gb: Total system memory in GB (128GB for DGX Spark)
        - system_memory_percent: Memory usage percentage
        - cpu_percent: CPU usage percentage
    """
    # Fetch DCGM metrics
    dcgm = await fetch_dcgm_metrics()

    # Fetch node metrics
    node = await fetch_node_metrics()

    # System memory (unified memory - shared by CPU and GPU)
    # DGX Spark GB10 has 128GB total system RAM
    system_memory_total_gb = 128.0
    system_memory_used_gb = node['memory_bytes'] / (1024 ** 3)
    system_memory_percent = (system_memory_used_gb / system_memory_total_gb) * 100

    # CPU usage (96 cores * 1000 millicores = 96000 total)
    cpu_total_millicores = 96000
    cpu_percent = (node['cpu_millicores'] / cpu_total_millicores) * 100

    return {
        # GPU metrics from DCGM
        "gpu_utilization": dcgm.get('DCGM_FI_DEV_GPU_UTIL', 0),
        "memory_bandwidth": dcgm.get('DCGM_FI_DEV_MEM_COPY_UTIL', 0),
        "gpu_temp": dcgm.get('DCGM_FI_DEV_GPU_TEMP', 0),
        "memory_temp": dcgm.get('DCGM_FI_DEV_MEMORY_TEMP', 0),
        "power_usage": dcgm.get('DCGM_FI_DEV_POWER_USAGE', 0),
        "sm_clock": dcgm.get('DCGM_FI_DEV_SM_CLOCK', 0),

        # System metrics (unified memory)
        "system_memory_used_gb": round(system_memory_used_gb, 2),
        "system_memory_total_gb": system_memory_total_gb,
        "system_memory_percent": round(system_memory_percent, 1),
        "cpu_percent": round(cpu_percent, 1),

        # Metadata
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "unified_memory": True,  # Indicates this is a unified memory system
    }
