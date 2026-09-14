"""The memory quota of an application's namespace.

An application without GPU work gets NON_GPU_QUOTA. With GPU work the quota
follows the cluster's GPU nodes, and the largest requirement among them wins:

- A unified-memory node (DGX Spark, GB10) charges GPU memory to the quota,
  because GPU memory is host RAM. Its limit is the node's allocatable memory,
  capped at the platform's AI budget, LLM_UMA_AI_BUDGET_GB.
- A discrete GPU node keeps VRAM outside the quota, so its limit covers host
  overhead only: DISCRETE_LIMIT_GI.

Both deploy paths use this module: the first deploy in deploy_application.py
and regeneration in manifest_generator.py. Each lists the nodes with its own
client and passes them in as plain dictionaries.
"""

from typing import Dict, List, Mapping, Tuple

NON_GPU_QUOTA = ("8Gi", "16Gi")
DISCRETE_LIMIT_GI = 16
MIN_REQUEST_GI = 8

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


def node_view(name: str, allocatable, labels) -> Dict:
    """A node as memory_quota reads it. A node must report its allocatable resources."""
    if allocatable is None:
        raise QuotaError(f"Node {name} reports no allocatable resources.")
    return {"name": name, "allocatable": dict(allocatable), "labels": dict(labels or {})}
