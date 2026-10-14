# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The memory and CPU quota of an application's namespace.

An application without GPU work gets NON_GPU_QUOTA and NON_GPU_CPU_QUOTA.
With GPU work the memory quota follows the cluster's GPU nodes, and the
largest requirement among them wins:

- A unified-memory node (DGX Spark, GB10) charges GPU memory to the quota,
  because GPU memory is host RAM. Its limit is the node's allocatable memory,
  capped at the platform's AI budget, LLM_UMA_AI_BUDGET_GB.
- A discrete GPU node keeps VRAM outside the quota, so its limit covers host
  overhead only: DISCRETE_LIMIT_GI.

The CPU quota of an application with GPU work holds one GPU pod per GPU
time-slice slot in the cluster (the nodes' allocatable nvidia.com/gpu), on
top of the fixed quota: a model server such as vLLM runs one pod per model,
and each model takes a slot. A GPU pod has the xlarge container size of the
deployment templates: 1 CPU requested, 4 the limit.

Whatever the computation above gives, the quota holds every container of the
application twice (namespace_quota): a rolling update runs the new pod
beside the old one, so a quota that holds the application once refuses its
updates. A container's resources are those of its size in the deployment
templates (CONTAINER_SIZES), or the LimitRange default when it has none.

Both deploy paths use this module: the first deploy in deploy_application.py
and regeneration in manifest_generator.py. Each lists the nodes with its own
client and passes them in as plain dictionaries.
"""

from typing import Dict, List, Mapping, Tuple

NON_GPU_QUOTA = ("8Gi", "16Gi")
DISCRETE_LIMIT_GI = 16
MIN_REQUEST_GI = 8

NON_GPU_CPU_QUOTA = ("4", "8")

# (requests memory Mi, requests cpu m, limits memory Mi, limits cpu m) of a
# container size, as templates/k8s/deployment-separate.j2 and
# knative-service.j2 render them; None: no size, the LimitRange default.
CONTAINER_SIZES = {
    "small": (128, 100, 256, 500),
    "medium": (256, 250, 512, 1000),
    "large": (512, 500, 1024, 2000),
    "xlarge": (4096, 1000, 16384, 4000),
    None: (64, 25, 256, 250),
}
ROLLOUT_COPIES = 2
GPU_POD_CPU_REQUEST = 1
GPU_POD_CPU_LIMIT = 4

GPU_RESOURCE = "nvidia.com/gpu"
FAMILY_LABEL = "nvidia.com/gpu.family"
PRODUCT_LABEL = "nvidia.com/gpu.product"


class QuotaError(RuntimeError):
    """The namespace quota cannot be computed from what the cluster reports."""


def uma_budget_gi(environ: Mapping[str, str]) -> int:
    value = environ.get("LLM_UMA_AI_BUDGET_GB")
    if not value:
        raise QuotaError(
            "LLM_UMA_AI_BUDGET_GB is not set. The thinkube-control backend Deployment sets it."
        )
    return int(float(value))


def _is_unified_memory(node: Dict) -> bool:
    labels = node["labels"]
    if FAMILY_LABEL not in labels or PRODUCT_LABEL not in labels:
        raise QuotaError(
            f"GPU node {node['name']} has no {FAMILY_LABEL} or {PRODUCT_LABEL} label, "
            "so whether its GPU memory is host RAM cannot be told. GPU feature "
            "discovery sets these labels."
        )
    family = labels[FAMILY_LABEL].lower()
    product = labels[PRODUCT_LABEL].lower()
    return "blackwell" in family or "gb10" in product or "dgx" in product


def _allocatable_gi(node: Dict) -> int:
    memory = node["allocatable"].get("memory")
    if not isinstance(memory, str) or not memory.endswith("Ki"):
        raise QuotaError(
            f"GPU node {node['name']} reports allocatable memory {memory!r}, not in Ki."
        )
    return int(round(float(memory[:-2]) / (1024 * 1024)))


def memory_quota(has_gpu: bool, nodes: List[Dict], environ: Mapping[str, str]) -> Tuple[str, str]:
    """(requests.memory, limits.memory) for the namespace.

    Each node is {"name": str, "allocatable": dict, "labels": dict}.
    """
    if not has_gpu:
        return NON_GPU_QUOTA

    limit_gi = 0
    for node in nodes:
        if int(node["allocatable"].get(GPU_RESOURCE, "0")) == 0:
            continue
        if _is_unified_memory(node):
            node_gi = min(_allocatable_gi(node), uma_budget_gi(environ))
        else:
            node_gi = DISCRETE_LIMIT_GI
        limit_gi = max(limit_gi, node_gi)

    if limit_gi == 0:
        raise QuotaError("The application requests a GPU, but no node in the cluster has one.")
    return (f"{max(limit_gi // 2, MIN_REQUEST_GI)}Gi", f"{limit_gi}Gi")


def cpu_quota(has_gpu: bool, nodes: List[Dict]) -> Tuple[str, str]:
    """(requests.cpu, limits.cpu) for the namespace.

    Each node is {"name": str, "allocatable": dict, "labels": dict}.
    """
    if not has_gpu:
        return NON_GPU_CPU_QUOTA
    slots = sum(int(node["allocatable"].get(GPU_RESOURCE, "0")) for node in nodes)
    if slots == 0:
        raise QuotaError("The application requests a GPU, but no node in the cluster has one.")
    requests, limits = NON_GPU_CPU_QUOTA
    return (str(int(requests) + slots * GPU_POD_CPU_REQUEST), str(int(limits) + slots * GPU_POD_CPU_LIMIT))


def node_view(name: str, allocatable, labels) -> Dict:
    """A node as memory_quota reads it. A node must report its allocatable resources."""
    if allocatable is None:
        raise QuotaError(f"Node {name} reports no allocatable resources.")
    return {"name": name, "allocatable": dict(allocatable), "labels": dict(labels or {})}


def _mi(value: str) -> int:
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024
    if value.endswith("Mi"):
        return int(value[:-2])
    raise QuotaError(f"Quota memory {value!r} is neither in Gi nor in Mi")


def _m(value: str) -> int:
    return int(value[:-1]) if value.endswith("m") else int(value) * 1000


def _memory(mi: int) -> str:
    return f"{mi // 1024}Gi" if mi % 1024 == 0 else f"{mi}Mi"


def _cpu(m: int) -> str:
    return str(m // 1000) if m % 1000 == 0 else f"{m}m"


def rollout_floor(containers: List[Dict]) -> Tuple[int, int, int, int]:
    """(requests Mi, requests m, limits Mi, limits m) holding every container ROLLOUT_COPIES times."""
    totals = [0, 0, 0, 0]
    for container in containers:
        size = container.get("size")
        if size not in CONTAINER_SIZES:
            raise QuotaError(f"Container {container.get('name')!r} has size {size!r}, not one of small, medium, large, xlarge")
        for i, value in enumerate(CONTAINER_SIZES[size]):
            totals[i] += value * ROLLOUT_COPIES
    return tuple(totals)


def namespace_quota(has_gpu: bool, nodes: List[Dict], environ: Mapping[str, str],
                    containers: List[Dict]) -> Tuple[str, str, str, str]:
    """(requests.memory, limits.memory, requests.cpu, limits.cpu) for the namespace."""
    req_mem, lim_mem = memory_quota(has_gpu, nodes, environ)
    req_cpu, lim_cpu = cpu_quota(has_gpu, nodes)
    floor_req_mem, floor_req_cpu, floor_lim_mem, floor_lim_cpu = rollout_floor(containers)
    return (
        _memory(max(_mi(req_mem), floor_req_mem)),
        _memory(max(_mi(lim_mem), floor_lim_mem)),
        _cpu(max(_m(req_cpu), floor_req_cpu)),
        _cpu(max(_m(lim_cpu), floor_lim_cpu)),
    )
