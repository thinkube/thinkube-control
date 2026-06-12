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

# Namespaces whose pods are AI workloads (excluded from platform_reserved).
AI_NAMESPACES = {"vllm", "ollama", "text-embeddings", "tensorrt-llm"}
NODE_ROLE_LABEL = "thinkube.io/node-role"  # "platform-shared" | "ai-dedicated"
PLATFORM_RESERVED_TTL = 30.0  # seconds to cache the per-node platform reservation

_MEM_BIN = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5}
_MEM_DEC = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}


def _quantity_to_bytes(q) -> float:
    """Parse a Kubernetes memory quantity (e.g. '16Gi', '512Mi', '2G', '1024') to bytes."""
    s = str(q).strip()
    if not s:
        return 0.0
    for suf, mult in _MEM_BIN.items():
        if s.endswith(suf):
            try:
                return float(s[:-2]) * mult
            except ValueError:
                return 0.0
    for suf, mult in _MEM_DEC.items():
        if s.endswith(suf):
            try:
                return float(s[:-1]) * mult
            except ValueError:
                return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


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
        self._platform_reserved_floor_gb = float(
            os.getenv("LLM_PLATFORM_RESERVED_FLOOR_GB", "16")
        )
        self._platform_reserved_cache: Dict[str, Tuple[float, float]] = {}
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

    def _platform_reserved_gb(self, node_name: str) -> float:
        """Host RAM held by non-AI pods on a node (UMA budgeting), floored. Cached."""
        now = time.monotonic()
        cached = self._platform_reserved_cache.get(node_name)
        if cached and (now - cached[1]) < PLATFORM_RESERVED_TTL:
            return cached[0]
        reserved = self._platform_reserved_floor_gb
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            pods = v1.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node_name},status.phase=Running"
            )
            total_bytes = 0.0
            for pod in pods.items:
                if (pod.metadata.namespace or "") in AI_NAMESPACES:
                    continue
                for c in (pod.spec.containers or []):
                    req = (c.resources.requests if c.resources else None) or {}
                    mem = req.get("memory")
                    if mem:
                        total_bytes += _quantity_to_bytes(mem)
            measured = total_bytes / (1024**3)
            reserved = max(self._platform_reserved_floor_gb, round(measured, 1))
        except Exception as e:
            logger.warning(f"platform_reserved compute failed for {node_name}: {e}")
        self._platform_reserved_cache[node_name] = (reserved, now)
        return reserved

    def _budget_for(
        self, node: GPUNode, total_gb: float, is_uma: bool
    ) -> Tuple[str, float, float]:
        """Return (arch, platform_reserved_gb, ai_budget_gb) for a node.

        UMA: ai_budget = total − platform_reserved (host RAM is quota-charged).
        Discrete: ai_budget = Σ per-GPU VRAM (not cgroup-charged), reserved = 0.
        """
        arch = "uma" if (is_uma or node.arch == "uma") else "discrete"
        if arch == "discrete":
            vram_total = (node.per_gpu_vram_gb or 0.0) * max(node.gpu_count, 1)
            ai_budget = vram_total if vram_total > 0 else total_gb
            return arch, 0.0, round(ai_budget, 1)
        reserved = (
            0.0 if node.role == "ai-dedicated" else self._platform_reserved_gb(node.name)
        )
        return arch, round(reserved, 1), round(max(total_gb - reserved, 0.0), 1)

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
            fits = (target_gb + margin) <= ai_budget if ai_budget else False
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
            fits = target_gb <= (per_vram * tp) if per_vram else False

        return {
            "arch": arch,
            "total_gb": total_gb,
            "ai_budget_gb": ai_budget,
            "platform_reserved_gb": reserved,
            "gpu_memory_utilization": util,
            "pod_mem_limit_gb": pod_mem,
            "tensor_parallel_size": tp,
            "fits": fits,
            "reason": (
                "ok"
                if fits
                else (
                    f"model needs ~{target_gb:.1f} GB (+~{margin:.0f} GB margin) "
                    f"but {node_name} ai_budget is {ai_budget:.1f} GB ({arch})"
                )
            ),
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
                        "real_available_gb": real_available_gb,
                        "metrics_available": True,
                        "per_gpu_metrics": per_gpu,
                        "allocations": list(allocs),
                        "available_slots": max(node.total_slots - slots_used, 0),
                    }
                )
            else:
                est_used = sum(a.estimated_memory_gb for a in allocs)
                arch, reserved, ai_budget = self._budget_for(
                    node, node.total_memory_gb, node.is_uma
                )
                updated = node.model_copy(
                    update={
                        "used_memory_gb": est_used,
                        "arch": arch,
                        "platform_reserved_gb": reserved,
                        "ai_budget_gb": ai_budget,
                        "available_slots": max(node.total_slots - slots_used, 0),
                        "allocations": list(allocs),
                        "metrics_available": False,
                    }
                )

            nodes.append(updated)
            total_mem += updated.total_memory_gb
            used_mem += updated.used_memory_gb

        # A node can accept a new model when its AI budget has headroom beyond
        # the memory already committed to loaded models on it.
        can_accept = any(
            (n.ai_budget_gb - sum(a.estimated_memory_gb for a in n.allocations)) > 4.0
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
