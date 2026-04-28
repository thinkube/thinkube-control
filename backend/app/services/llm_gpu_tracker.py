import json
import logging
import os
from typing import Dict, List, Optional, Tuple

from app.api.llm.schemas import (
    GPUAllocation,
    GPUNode,
    GPUStatusResponse,
)

logger = logging.getLogger(__name__)


class LLMGPUTracker:
    def __init__(self):
        self._memory_threshold = float(
            os.getenv("LLM_GPU_MEMORY_THRESHOLD", "0.85")
        )
        self._memory_map = self._parse_memory_map()
        self._fallback_memory_gb = float(
            os.getenv("LLM_GPU_TOTAL_MEMORY_GB", "128")
        )
        self._gpu_nodes: Dict[str, GPUNode] = {}
        self._allocations: Dict[str, List[GPUAllocation]] = {}
        self._discover_gpu_nodes()

    def _parse_memory_map(self) -> Dict[str, float]:
        raw = os.getenv("LLM_GPU_MEMORY_MAP", "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Failed to parse LLM_GPU_MEMORY_MAP: {e}")
            return {}

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

                # gpu.memory label and memory_map values are per-GPU
                if gpu_memory_mb:
                    per_gpu_memory = float(gpu_memory_mb) / 1024.0
                elif product in self._memory_map:
                    per_gpu_memory = self._memory_map[product]
                else:
                    per_gpu_memory = self._fallback_memory_gb

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
                    f"({gpu_count}x, {per_gpu_memory:.0f}GB/GPU, "
                    f"{total_memory:.0f}GB total, {gpu_slots} slots)"
                )

        except Exception as e:
            logger.warning(f"GPU node discovery failed, using fallback: {e}")
            fallback_total = max(self._fallback_memory_gb, 1.0)
            self._gpu_nodes["gpu-pool"] = GPUNode(
                name="gpu-pool",
                total_slots=4,
                available_slots=4,
                total_memory_gb=fallback_total,
                per_gpu_memory_gb=fallback_total,
                used_memory_gb=0.0,
            )
            self._allocations["gpu-pool"] = []

    def list_nodes(self) -> List[GPUNode]:
        return list(self._gpu_nodes.values())

    def get_node(self, node_name: str) -> Optional[GPUNode]:
        return self._gpu_nodes.get(node_name)

    def get_status(self) -> GPUStatusResponse:
        nodes = []
        total_mem = 0.0
        used_mem = 0.0

        for name, node in self._gpu_nodes.items():
            allocs = self._allocations.get(name, [])
            node_used = sum(a.estimated_memory_gb for a in allocs)
            node_slots_used = sum(a.slots for a in allocs)

            updated = node.model_copy(update={
                "used_memory_gb": node_used,
                "available_slots": max(node.total_slots - node_slots_used, 0),
                "allocations": list(allocs),
            })
            nodes.append(updated)
            total_mem += node.total_memory_gb
            used_mem += node_used

        can_accept = any(
            n.used_memory_gb / n.total_memory_gb < self._memory_threshold
            for n in nodes
            if n.total_memory_gb > 0
        )

        return GPUStatusResponse(
            nodes=nodes,
            total_memory_gb=total_mem,
            used_memory_gb=used_mem,
            memory_threshold=self._memory_threshold,
            can_accept_new_model=can_accept,
        )

    def check_can_load(
        self,
        estimated_memory_gb: float,
        node_name: Optional[str] = None,
        slots_needed: int = 1,
    ) -> Tuple[bool, str]:
        if node_name:
            return self._check_node(node_name, estimated_memory_gb, slots_needed)

        for name in self._gpu_nodes:
            ok, reason = self._check_node(name, estimated_memory_gb, slots_needed)
            if ok:
                return True, name
        return False, "No GPU node has sufficient resources"

    def _check_node(
        self, node_name: str, estimated_memory_gb: float, slots_needed: int
    ) -> Tuple[bool, str]:
        import math

        node = self._gpu_nodes.get(node_name)
        if not node:
            return False, f"Node '{node_name}' not found"

        allocs = self._allocations.get(node_name, [])
        used = sum(a.estimated_memory_gb for a in allocs)
        used_slots = sum(a.slots for a in allocs)

        per_gpu = node.per_gpu_memory_gb
        if per_gpu <= 0:
            per_gpu = node.total_memory_gb / max(node.gpu_count, 1)

        if node.shared_memory:
            # Time-sliced: models share physical memory pool
            projected = (used + estimated_memory_gb) / node.total_memory_gb
            if projected > self._memory_threshold:
                return False, (
                    f"Would exceed memory on {node_name}: "
                    f"{used:.1f} + {estimated_memory_gb:.1f} = {used + estimated_memory_gb:.1f}GB "
                    f"(limit: {self._memory_threshold * node.total_memory_gb:.1f}GB)"
                )
            if node.total_slots - used_slots < slots_needed:
                return False, f"Not enough slots on {node_name}: need {slots_needed}, have {node.total_slots - used_slots}"
        else:
            # Discrete GPUs: model must fit within N GPUs (tensor parallelism)
            gpus_needed = math.ceil(estimated_memory_gb / (per_gpu * self._memory_threshold))
            if gpus_needed > node.gpu_count:
                return False, (
                    f"Model needs ~{estimated_memory_gb:.1f}GB but {node_name} only has "
                    f"{node.gpu_count}x {per_gpu:.0f}GB GPUs ({node.total_memory_gb:.0f}GB total)"
                )
            actual_slots_needed = max(slots_needed, gpus_needed)
            if node.total_slots - used_slots < actual_slots_needed:
                return False, (
                    f"Not enough GPU slots on {node_name}: need {actual_slots_needed}, "
                    f"have {node.total_slots - used_slots}"
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
            f"GPU allocation: {model_id} on {backend_id}@{target} "
            f"({estimated_memory_gb:.1f}GB, {slots} slot(s))"
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
        return "gpu-pool"


llm_gpu_tracker = LLMGPUTracker()
