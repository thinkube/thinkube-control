import logging
import os
from typing import Dict, List, Optional, Tuple

import httpx

from app.api.llm.schemas import (
    GPUAllocation,
    GPUNode,
    GPUStatusResponse,
)

logger = logging.getLogger(__name__)

NODE_METRICS_PORT = 9100
NODE_METRICS_NAMESPACE = "thinkube-control"


class LLMGPUTracker:
    def __init__(self):
        self._memory_threshold = float(
            os.getenv("LLM_GPU_MEMORY_THRESHOLD", "0.85")
        )
        self._gpu_nodes: Dict[str, GPUNode] = {}
        self._allocations: Dict[str, List[GPUAllocation]] = {}
        self._node_pod_ips: Dict[str, str] = {}
        self._pod_ips_discovered = False
        self._discover_gpu_nodes()

    def _discover_gpu_nodes(self):
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            nodes = v1.list_node()

            for node in nodes.items:
                allocatable = node.status.allocatable or {}
                gpu_slots = int(allocatable.get("nvidia.com/gpu", "0"))
                if gpu_slots == 0:
                    continue

                labels = node.metadata.labels or {}
                name = node.metadata.name
                product = labels.get("nvidia.com/gpu.product", "")
                family = labels.get("nvidia.com/gpu.family", "")
                gpu_count = int(labels.get("nvidia.com/gpu.count", "1"))
                gpu_replicas = int(labels.get("nvidia.com/gpu.replicas", "1"))
                gpu_memory_mb = labels.get("nvidia.com/gpu.memory", "")
                sharing = labels.get("nvidia.com/gpu.sharing-strategy", "none")

                if gpu_memory_mb and gpu_memory_mb != "0":
                    per_gpu_memory = float(gpu_memory_mb) / 1024.0
                else:
                    per_gpu_memory = 0.0

                total_memory = per_gpu_memory * gpu_count

                self._gpu_nodes[name] = GPUNode(
                    name=name,
                    gpu_product=product or None,
                    gpu_family=family or None,
                    gpu_count=gpu_count,
                    gpu_replicas=gpu_replicas,
                    total_slots=gpu_slots,
                    available_slots=gpu_slots,
                    total_memory_gb=total_memory,
                    per_gpu_memory_gb=per_gpu_memory,
                    used_memory_gb=0.0,
                    shared_memory=(sharing == "time-slicing"),
                    allocations=[],
                )
                self._allocations[name] = []
                logger.info(
                    f"GPU node discovered: {name} — {product} "
                    f"({gpu_count}x, {gpu_slots} slots)"
                )

        except Exception as e:
            logger.error(f"GPU node discovery failed: {e}")

    async def _ensure_pod_ips(self):
        if not self._pod_ips_discovered:
            self._discover_node_metrics_pods()

    def _discover_node_metrics_pods(self):
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            pods = v1.list_namespaced_pod(
                NODE_METRICS_NAMESPACE,
                label_selector="app=node-metrics",
            )
            for pod in pods.items:
                if (
                    pod.status.phase == "Running"
                    and pod.status.pod_ip
                    and pod.spec.node_name
                ):
                    self._node_pod_ips[pod.spec.node_name] = pod.status.pod_ip
                    logger.info(
                        f"Node-metrics pod: {pod.spec.node_name} -> {pod.status.pod_ip}"
                    )
            self._pod_ips_discovered = True
        except Exception as e:
            logger.warning(f"Failed to discover node-metrics pods: {e}")

    async def fetch_node_metrics(self, node_name: str) -> Optional[dict]:
        await self._ensure_pod_ips()
        pod_ip = self._node_pod_ips.get(node_name)
        if not pod_ip:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"http://{pod_ip}:{NODE_METRICS_PORT}/metrics"
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch metrics from {node_name} ({pod_ip}): {e}")
            return None

    def list_nodes(self) -> List[GPUNode]:
        return list(self._gpu_nodes.values())

    def get_node(self, node_name: str) -> Optional[GPUNode]:
        return self._gpu_nodes.get(node_name)

    def is_uma(self, node_name: str) -> bool:
        node = self._gpu_nodes.get(node_name)
        if node:
            return node.is_uma
        return False

    async def get_status(self) -> GPUStatusResponse:
        nodes = []
        total_mem = 0.0
        used_mem = 0.0

        for name, node in self._gpu_nodes.items():
            allocs = self._allocations.get(name, [])
            slots_used = sum(a.slots for a in allocs)
            metrics = await self.fetch_node_metrics(name)

            if metrics:
                is_uma = metrics.get("is_uma", False)
                allocatable_bytes = metrics.get("gpu_allocatable_bytes", 0)
                real_available_gb = round(allocatable_bytes / (1024**3), 1)

                if is_uma:
                    real_total_gb = round(
                        metrics.get("memory_total_bytes", 0) / (1024**3), 1
                    )
                else:
                    gpu_total_mb = metrics.get("gpu_memory_total_mb", 0)
                    real_total_gb = (
                        round(gpu_total_mb / 1024.0, 1)
                        if gpu_total_mb > 0
                        else node.total_memory_gb
                    )

                real_used_gb = round(max(real_total_gb - real_available_gb, 0), 1)

                updated = node.model_copy(
                    update={
                        "total_memory_gb": real_total_gb,
                        "used_memory_gb": real_used_gb,
                        "is_uma": is_uma,
                        "real_available_gb": real_available_gb,
                        "metrics_available": True,
                        "allocations": list(allocs),
                        "available_slots": max(node.total_slots - slots_used, 0),
                    }
                )
            else:
                est_used = sum(a.estimated_memory_gb for a in allocs)
                updated = node.model_copy(
                    update={
                        "used_memory_gb": est_used,
                        "available_slots": max(node.total_slots - slots_used, 0),
                        "allocations": list(allocs),
                        "metrics_available": False,
                    }
                )

            nodes.append(updated)
            total_mem += updated.total_memory_gb
            used_mem += updated.used_memory_gb

        can_accept = any(
            n.metrics_available and (n.real_available_gb or 0) > 4.0
            for n in nodes
        )

        return GPUStatusResponse(
            nodes=nodes,
            total_memory_gb=round(total_mem, 1),
            used_memory_gb=round(used_mem, 1),
            memory_threshold=self._memory_threshold,
            can_accept_new_model=can_accept,
        )

    async def check_can_load(
        self,
        estimated_memory_gb: float,
        node_name: Optional[str] = None,
        slots_needed: int = 1,
    ) -> Tuple[bool, str]:
        if node_name:
            return await self._check_node(node_name, estimated_memory_gb)

        for name in self._gpu_nodes:
            ok, reason = await self._check_node(name, estimated_memory_gb)
            if ok:
                return True, name
        return False, "No GPU node has sufficient resources"

    async def _check_node(
        self, node_name: str, estimated_memory_gb: float
    ) -> Tuple[bool, str]:
        node = self._gpu_nodes.get(node_name)
        if not node:
            return False, f"Node '{node_name}' not found"

        metrics = await self.fetch_node_metrics(node_name)
        if metrics is None:
            return False, (
                f"Cannot verify GPU resources on {node_name}: "
                f"metrics unavailable — refusing to load"
            )

        allocatable_bytes = metrics.get("gpu_allocatable_bytes", 0)
        allocatable_gb = allocatable_bytes / (1024**3)
        usable_gb = allocatable_gb * self._memory_threshold

        if estimated_memory_gb > usable_gb:
            return False, (
                f"Insufficient GPU memory on {node_name}: "
                f"model needs ~{estimated_memory_gb:.1f} GB, "
                f"allocatable {allocatable_gb:.1f} GB "
                f"(usable at {self._memory_threshold * 100:.0f}%: {usable_gb:.1f} GB)"
            )
        return True, "ok"

    def record_allocation(
        self,
        model_id: str,
        backend_id: str,
        estimated_memory_gb: float,
        node_name: Optional[str] = None,
        slots: int = 1,
    ):
        target = node_name or self._pick_default_node()
        if target not in self._allocations:
            self._allocations[target] = []

        self._allocations[target] = [
            a for a in self._allocations[target] if a.model_id != model_id
        ]
        self._allocations[target].append(
            GPUAllocation(
                model_id=model_id,
                backend_id=backend_id,
                node_name=target,
                estimated_memory_gb=estimated_memory_gb,
                slots=slots,
            )
        )
        logger.info(
            f"GPU allocation recorded: {model_id} on {backend_id}@{target} "
            f"({estimated_memory_gb:.1f} GB, {slots} slot(s))"
        )

    def release_allocation(self, model_id: str):
        for name, allocs in self._allocations.items():
            before = len(allocs)
            self._allocations[name] = [a for a in allocs if a.model_id != model_id]
            if len(self._allocations[name]) < before:
                logger.info(f"GPU allocation released: {model_id} from {name}")
                return

    def get_eviction_candidates(self) -> List[GPUAllocation]:
        all_allocs = []
        for allocs in self._allocations.values():
            all_allocs.extend(allocs)
        return sorted(all_allocs, key=lambda a: a.estimated_memory_gb, reverse=True)

    def _pick_default_node(self) -> str:
        if self._gpu_nodes:
            return next(iter(self._gpu_nodes))
        return "unknown"


llm_gpu_tracker = LLMGPUTracker()
