import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import httpx

from app.api.llm.schemas import (
    GPUAllocation,
    GPUMetricEntry,
    GPUNode,
    GPUStatusResponse,
)

logger = logging.getLogger(__name__)

NODE_METRICS_PORT = 9100
NODE_METRICS_NAMESPACE = "thinkube-control"

NODE_ROLE_LABEL = "thinkube.io/node-role"  # "platform-shared" | "ai-dedicated"

# UMA nodes (DGX Spark): GPU memory IS host RAM, so the AI budget is a fixed
# ceiling that leaves the rest for the OS/kernel/platform — never the whole
# device. Default 96 GB on a 128 GB Spark (≈32 GB reserved for the system).
UMA_AI_BUDGET_GB = float(os.getenv("LLM_UMA_AI_BUDGET_GB", "96"))

# Backends that lock one time-slice slot PER model (each is its own pod).
# Ollama is excluded: one Ollama pod hosts many models in a single slot.
SLOT_PER_MODEL_BACKENDS = ("vllm", "tensorrt-llm", "text-embeddings")


def _slots_used(allocs) -> int:
    """Time-slice slots consumed on a node, backend-aware.

    vLLM / TensorRT / TEI lock one slot per model; Ollama collapses to a single
    shared slot however many models it hosts.
    """
    per_model = sum(
        a.slots for a in allocs if not str(a.backend_id).startswith("ollama")
    )
    has_ollama = any(str(a.backend_id).startswith("ollama") for a in allocs)
    return per_model + (1 if has_ollama else 0)


def _reserved_gb(allocs) -> float:
    """Memory reserved up front on a node. vLLM/TensorRT/TEI reserve per model;
    Ollama self-manages its models' memory (keep_alive), so it isn't summed here.
    """
    return sum(
        a.estimated_memory_gb
        for a in allocs
        if not str(a.backend_id).startswith("ollama")
    )


class LLMGPUTracker:
    def __init__(self):
        self._memory_threshold = float(
            os.getenv("LLM_GPU_MEMORY_THRESHOLD", "0.85")
        )
        self._gpu_nodes: Dict[str, GPUNode] = {}
        self._allocations: Dict[str, List[GPUAllocation]] = {}
        self._node_pod_ips: Dict[str, str] = {}
        self._pod_ips_discovered = False
        self._last_node_discovery = 0.0
        self._discover_gpu_nodes()

    def refresh_nodes(self) -> int:
        """Re-discover GPU nodes (e.g. after a node is added to the cluster).

        Merge-safe: existing allocations are preserved, new nodes are added,
        and nodes that no longer exist are dropped. Returns the node count.
        """
        self._discover_gpu_nodes()
        return len(self._gpu_nodes)

    def _discover_gpu_nodes(self):
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            nodes = v1.list_node()

            discovered = set()
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

                role = labels.get(NODE_ROLE_LABEL, "platform-shared")
                fam_l = (family or "").lower()
                prod_l = (product or "").lower()
                arch = (
                    "uma"
                    if ("blackwell" in fam_l or "gb10" in prod_l or "dgx" in prod_l)
                    else "discrete"
                )

                discovered.add(name)
                is_new = name not in self._gpu_nodes
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
                    per_gpu_vram_gb=per_gpu_memory,
                    arch=arch,
                    role=role,
                    used_memory_gb=0.0,
                    shared_memory=(sharing == "time-slicing"),
                    allocations=[],
                )
                # Preserve existing allocations across re-discovery.
                self._allocations.setdefault(name, [])
                if is_new:
                    logger.info(
                        f"GPU node discovered: {name} — {product} "
                        f"({gpu_count}x, {gpu_slots} slots)"
                    )

            # Drop nodes that no longer exist in the cluster.
            for gone in set(self._gpu_nodes) - discovered:
                self._gpu_nodes.pop(gone, None)
                self._allocations.pop(gone, None)
                logger.info(f"GPU node removed (no longer in cluster): {gone}")

        except Exception as e:
            logger.error(f"GPU node discovery failed: {e}")
        finally:
            self._last_node_discovery = time.monotonic()

    async def _ensure_pod_ips(self, node_name: Optional[str] = None):
        if not self._pod_ips_discovered:
            self._discover_node_metrics_pods()
        elif node_name and node_name not in self._node_pod_ips:
            # Re-discover if a specific node is missing (added after startup)
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
        await self._ensure_pod_ips(node_name)
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

    def _budget_for(
        self, node: GPUNode, total_gb: float, is_uma: bool
    ) -> Tuple[str, float, float]:
        """Return (arch, reserved_gb, ai_budget_gb) for a node.

        UMA: ai_budget is a fixed ceiling (UMA_AI_BUDGET_GB, clamped to the
        node's total) because GPU memory is host RAM — the rest is left for the
        OS/kernel/platform so the node can never be driven into OOM. `reserved`
        is the derived remainder (total − budget), for display only.
        Discrete: ai_budget = Σ per-GPU VRAM (not cgroup-charged), reserved = 0.
        """
        arch = "uma" if (is_uma or node.arch == "uma") else "discrete"
        if arch == "discrete":
            vram_total = (node.per_gpu_vram_gb or 0.0) * max(node.gpu_count, 1)
            ai_budget = vram_total if vram_total > 0 else total_gb
            return arch, 0.0, round(ai_budget, 1)
        ai_budget = min(total_gb, UMA_AI_BUDGET_GB) if total_gb else UMA_AI_BUDGET_GB
        reserved = round(max(total_gb - ai_budget, 0.0), 1)
        return arch, reserved, round(ai_budget, 1)

    async def _effective_total_gb(
        self, node: GPUNode, metrics: Optional[dict]
    ) -> Tuple[float, bool]:
        """Best estimate of the node's total GPU memory + whether it's UMA."""
        is_uma = metrics.get("is_uma", node.is_uma) if metrics else node.is_uma
        if metrics:
            if is_uma:
                total = metrics.get("memory_total_bytes", 0) / (1024**3)
            else:
                gpu_mb = metrics.get("gpu_memory_total_mb", 0)
                total = (gpu_mb / 1024.0) if gpu_mb > 0 else (
                    (node.per_gpu_vram_gb or 0.0) * max(node.gpu_count, 1)
                )
        else:
            total = node.total_memory_gb or (
                (node.per_gpu_vram_gb or 0.0) * max(node.gpu_count, 1)
            )
        return round(total or 0.0, 1), is_uma

    async def plan_sizing(
        self, node_name: str, target_gb: float, gpu_count: int = 1
    ) -> dict:
        """Translate a model footprint into arch-correct vLLM/pod knobs.

        Returns a dict with arch, total_gb, ai_budget_gb, gpu_memory_utilization,
        pod_mem_limit_gb, tensor_parallel_size, fits, reason. The gateway plans
        strictly within ai_budget — fits is False when the footprint (+ margin)
        exceeds it.
        """
        node = self._gpu_nodes.get(node_name)
        if not node:
            return {"fits": False, "reason": f"Node '{node_name}' not found"}

        metrics = await self.fetch_node_metrics(node_name)
        total_gb, is_uma = await self._effective_total_gb(node, metrics)
        arch, reserved, ai_budget = self._budget_for(node, total_gb, is_uma)
        margin = max(4.0, round(0.2 * target_gb, 1))

        # Co-residency: time-slices share the pool, so fit against what's LEFT
        # after the models already reserved on this node, and against free slots.
        allocs = self._allocations.get(node_name, [])
        reserved_on_node = _reserved_gb(allocs)
        free_slots = max(node.total_slots - _slots_used(allocs), 0)

        if arch == "uma":
            # GPU memory == host RAM: util is a fraction of the whole device,
            # and the cgroup/quota must cover target + margin.
            util = (
                min(max(round(target_gb / total_gb, 2), 0.05), 0.95)
                if total_gb
                else 0.0
            )
            pod_mem = int(round(target_gb + margin))
            tp = 1
            remaining = max(ai_budget - reserved_on_node, 0.0)
            fits = (target_gb + margin) <= remaining and free_slots >= 1
        else:
            # Discrete VRAM: util is per-GPU; cgroup only needs host overhead.
            per_vram = node.per_gpu_vram_gb or (total_gb / max(node.gpu_count, 1))
            tp = max(gpu_count, 1)
            per_gpu_target = (target_gb / tp) if tp else target_gb
            util = (
                min(max(round(per_gpu_target / per_vram, 2), 0.05), 0.95)
                if per_vram
                else 0.9
            )
            pod_mem = int(round(max(8.0, 0.25 * target_gb + 4.0)))
            remaining = max((per_vram * tp) - reserved_on_node, 0.0)
            fits = target_gb <= remaining and free_slots >= tp

        if fits:
            reason = "ok"
        elif free_slots < (1 if arch == "uma" else tp):
            reason = f"{node_name} has no free time-slice slot ({node.total_slots} in use)"
        else:
            reason = (
                f"model needs ~{target_gb:.1f} GB (+~{margin:.0f} GB margin) but "
                f"{node_name} has ~{remaining:.1f} GB free of {ai_budget:.1f} GB "
                f"AI budget ({arch})"
            )

        return {
            "arch": arch,
            "total_gb": total_gb,
            "ai_budget_gb": ai_budget,
            "ai_remaining_gb": round(remaining, 1),
            "reserved_gb": reserved,
            "free_slots": free_slots,
            "gpu_memory_utilization": util,
            "pod_mem_limit_gb": pod_mem,
            "tensor_parallel_size": tp,
            "fits": fits,
            "reason": reason,
        }

    async def get_status(self) -> GPUStatusResponse:
        # Self-heal: if no GPU nodes are known (e.g. the backend started
        # before any GPU node joined the cluster), retry discovery — throttled
        # so a genuinely GPU-less cluster isn't polled on every request.
        if not self._gpu_nodes and (
            time.monotonic() - self._last_node_discovery
        ) > 30:
            self._discover_gpu_nodes()

        nodes = []
        total_mem = 0.0
        used_mem = 0.0

        for name, node in self._gpu_nodes.items():
            allocs = self._allocations.get(name, [])
            slots_used = _slots_used(allocs)
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

                per_gpu = [
                    GPUMetricEntry(**g)
                    for g in metrics.get("gpus", [])
                ]

                arch, reserved, ai_budget = self._budget_for(node, real_total_gb, is_uma)
                updated = node.model_copy(
                    update={
                        "total_memory_gb": real_total_gb,
                        "used_memory_gb": real_used_gb,
                        "is_uma": is_uma,
                        "arch": arch,
                        "platform_reserved_gb": reserved,
                        "ai_budget_gb": ai_budget,
                        "ai_remaining_gb": round(max(ai_budget - _reserved_gb(allocs), 0.0), 1),
                        "real_available_gb": real_available_gb,
                        "metrics_available": True,
                        "per_gpu_metrics": per_gpu,
                        "allocations": list(allocs),
                        "available_slots": max(node.total_slots - slots_used, 0),
                    }
                )
            else:
                est_used = _reserved_gb(allocs)
                arch, reserved, ai_budget = self._budget_for(
                    node, node.total_memory_gb, node.is_uma
                )
                updated = node.model_copy(
                    update={
                        "used_memory_gb": est_used,
                        "arch": arch,
                        "platform_reserved_gb": reserved,
                        "ai_budget_gb": ai_budget,
                        "ai_remaining_gb": round(max(ai_budget - _reserved_gb(allocs), 0.0), 1),
                        "available_slots": max(node.total_slots - slots_used, 0),
                        "allocations": list(allocs),
                        "metrics_available": False,
                    }
                )

            nodes.append(updated)
            total_mem += updated.total_memory_gb
            used_mem += updated.used_memory_gb

        # A node can accept a new model when its AI budget has headroom beyond
        # the memory already reserved by co-resident models, and it has a free
        # time-slice slot. Ollama's self-managed memory isn't counted as reserved.
        can_accept = any(
            (n.ai_budget_gb - _reserved_gb(n.allocations)) > 4.0
            and n.available_slots > 0
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
