"""Cluster resources API for real-time resource availability"""

import logging
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from kubernetes import client, config
from kubernetes.stream import stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cluster", tags=["cluster-resources"])

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
    """Get real-time cluster resource availability including GPU details"""

    try:
        # Load kubernetes config
        try:
            config.load_incluster_config()
        except:
            # Fallback for local development
            config.load_kube_config()

        v1 = client.CoreV1Api()

        # Get all nodes
        nodes = v1.list_node()

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

            # Calculate allocated resources from pods
            pods = v1.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node_name}"
            )

            allocated_cpu = 0
            allocated_memory = 0
            allocated_gpu = 0

            for pod in pods.items:
                # Skip terminated pods
                if pod.status.phase in ["Succeeded", "Failed"]:
                    continue

                for container in pod.spec.containers:
                    if container.resources:
                        if container.resources.limits:
                            # CPU
                            cpu_limit = container.resources.limits.get("cpu", "0")
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
                            mem_limit = container.resources.limits.get("memory", "0")
                            if mem_limit != "0":
                                allocated_memory += parse_memory(mem_limit)

                            # GPU
                            gpu_limit = container.resources.limits.get("nvidia.com/gpu", "0")
                            if gpu_limit != "0":
                                allocated_gpu += int(gpu_limit)

            # Get GPU details if node has GPUs
            gpu_details = []
            if capacity["gpu"] > 0:
                try:
                    gpu_details = await get_gpu_details(node_name, v1)
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

            result.append({
                "name": node_name,
                "capacity": {
                    "cpu": capacity["cpu"],
                    "memory": format_memory(capacity["memory"]),
                    "gpu": capacity["gpu"]
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

    except Exception as e:
        logger.error(f"Failed to get cluster resources: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def get_gpu_details(node_name: str, v1: client.CoreV1Api) -> List[Dict[str, Any]]:
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
            tty=False
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