"""
LLM GPU Resource Tracker

Tracks GPU memory usage and allocation for model lifecycle decisions.
Supports both shared memory (DGX Spark) and dedicated GPU (consumer cards) topologies.
"""

import logging
import os
from typing import List, Optional, Tuple

from app.api.llm.schemas import (
    GPUAllocation,
    GPUNode,
    GPUStatusResponse,
)

logger = logging.getLogger(__name__)


class LLMGPUTracker:
    def __init__(self):
        self._total_memory_gb = float(
            os.getenv("LLM_GPU_TOTAL_MEMORY_GB", "128")
        )
        self._shared_memory = os.getenv("LLM_GPU_SHARED_MEMORY", "true").lower() == "true"
        self._memory_threshold = float(
            os.getenv("LLM_GPU_MEMORY_THRESHOLD", "0.85")
        )
        self._idle_timeout_minutes = int(
            os.getenv("LLM_MODEL_IDLE_TIMEOUT_MINUTES", "30")
        )
        self._allocations: List[GPUAllocation] = []
        self._total_slots = self._detect_gpu_slots()

    def _detect_gpu_slots(self) -> int:
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            nodes = v1.list_node()
            total_slots = 0
            for node in nodes.items:
                allocatable = node.status.allocatable or {}
                gpu_str = allocatable.get("nvidia.com/gpu", "0")
                total_slots += int(gpu_str)
            return max(total_slots, 1)
        except Exception as e:
            logger.warning(f"Could not detect GPU slots: {e}")
            return 4

    def get_status(self) -> GPUStatusResponse:
        used_memory = sum(a.estimated_memory_gb for a in self._allocations)
        available_slots = self._total_slots - sum(a.slots for a in self._allocations)

        node = GPUNode(
            name="gpu-pool",
            total_slots=self._total_slots,
            available_slots=max(available_slots, 0),
            total_memory_gb=self._total_memory_gb,
            used_memory_gb=used_memory,
            shared_memory=self._shared_memory,
            allocations=list(self._allocations),
        )

        can_accept = (
            used_memory / self._total_memory_gb < self._memory_threshold
            if self._total_memory_gb > 0
            else False
        )

        return GPUStatusResponse(
            nodes=[node],
            total_memory_gb=self._total_memory_gb,
            used_memory_gb=used_memory,
            memory_threshold=self._memory_threshold,
            can_accept_new_model=can_accept,
        )

    def check_can_load(
        self, estimated_memory_gb: float, slots_needed: int = 1
    ) -> Tuple[bool, str]:
        status = self.get_status()
        node = status.nodes[0]

        if node.available_slots < slots_needed:
            return False, f"Not enough GPU slots: need {slots_needed}, have {node.available_slots}"

        projected_usage = (
            status.used_memory_gb + estimated_memory_gb
        ) / self._total_memory_gb
        if projected_usage > self._memory_threshold:
            return False, (
                f"Would exceed memory threshold: "
                f"{status.used_memory_gb:.1f} + {estimated_memory_gb:.1f} = "
                f"{status.used_memory_gb + estimated_memory_gb:.1f}GB "
                f"(threshold: {self._memory_threshold * self._total_memory_gb:.1f}GB)"
            )

        return True, "ok"

    def record_allocation(
        self, model_id: str, backend_id: str, estimated_memory_gb: float, slots: int = 1
    ):
        self._allocations = [
            a for a in self._allocations if a.model_id != model_id
        ]
        self._allocations.append(
            GPUAllocation(
                model_id=model_id,
                backend_id=backend_id,
                estimated_memory_gb=estimated_memory_gb,
                slots=slots,
            )
        )
        logger.info(
            f"GPU allocation recorded: {model_id} on {backend_id} "
            f"({estimated_memory_gb:.1f}GB, {slots} slot(s))"
        )

    def release_allocation(self, model_id: str):
        before = len(self._allocations)
        self._allocations = [
            a for a in self._allocations if a.model_id != model_id
        ]
        if len(self._allocations) < before:
            logger.info(f"GPU allocation released: {model_id}")

    def get_eviction_candidates(self) -> List[GPUAllocation]:
        return sorted(self._allocations, key=lambda a: a.estimated_memory_gb, reverse=True)


llm_gpu_tracker = LLMGPUTracker()
