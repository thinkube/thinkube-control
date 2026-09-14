"""What a notebook server may be given on each node.

JupyterHub's Server Options form offers CPU and memory from fixed lists and
refuses any value that is not one of its choices, so the defaults stored per
node and the values thinkube-control asks the Hub for come from the same
lists, cut to what the node has.
"""

from __future__ import annotations

from typing import Any, Dict, List

CPU_CHOICES = [1, 2, 4, 6, 8, 12, 16, 24, 32]
MEMORY_CHOICES_GB = [2, 4, 8, 16, 32, 48, 64, 96, 128]

FALLBACK_CPU_CORES = 4
FALLBACK_MEMORY_GB = 8
FALLBACK_GPUS = 0


def gb(memory: str) -> float:
    """A Kubernetes memory string as gigabytes."""
    units = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1, "Ti": 1024, "K": 1e3 / 2**30, "M": 1e6 / 2**30, "G": 1e9 / 2**30}
    memory = str(memory)
    for suffix, factor in units.items():
        if memory.endswith(suffix):
            return float(memory[: -len(suffix)]) * factor
    return float(memory) / 2**30


def node_capacity(node: Dict[str, Any]) -> Dict[str, int]:
    """CPU cores, memory in GB and GPUs a server on this node can be given at most."""
    cap = node["capacity"]
    return {
        "cpu_cores": int(cap["cpu"]),
        "memory_gb": int(gb(cap["memory"])),
        "gpus": int(cap.get("effective_gpu", cap.get("gpu", 0))),
    }


def node_choices(node: Dict[str, Any]) -> Dict[str, List[int]]:
    capacity = node_capacity(node)
    return {
        "cpu_cores": [c for c in CPU_CHOICES if c <= capacity["cpu_cores"]] or [CPU_CHOICES[0]],
        "memory_gb": [m for m in MEMORY_CHOICES_GB if m <= capacity["memory_gb"]] or [MEMORY_CHOICES_GB[0]],
        "gpus": list(range(0, capacity["gpus"] + 1)),
    }


def nearest_choice(value: int, choices: List[int]) -> int:
    """The largest choice not above the value, or the smallest choice."""
    below = [c for c in choices if c <= value]
    return max(below) if below else min(choices)


def fit_to_node(node: Dict[str, Any], cpu_cores: int, memory_gb: int, gpus: int) -> Dict[str, int]:
    """Values moved onto the node's choices, for defaults saved before the node changed."""
    choices = node_choices(node)
    return {
        "cpu_cores": nearest_choice(cpu_cores, choices["cpu_cores"]),
        "memory_gb": nearest_choice(memory_gb, choices["memory_gb"]),
        "gpus": nearest_choice(gpus, choices["gpus"]),
    }
