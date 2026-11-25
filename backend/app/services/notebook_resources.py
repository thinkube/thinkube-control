# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""What a notebook server may be given on each node.

JupyterHub's Server Options form offers CPU, memory and GPU memory from fixed
lists and refuses any value that is not one of its choices, so the defaults
stored per node and the values thinkube-control asks the Hub for come from the
same lists, cut to what the node has.

A server's GPUs are the Scheduler's to give: JupyterHub asks it for them when
the server starts. A server asks for a number of the node's GPUs, and may say
the memory it takes on each, with which it shares the GPU with other work that
fits beside it; without it the server has its GPUs alone.
"""

from __future__ import annotations

from typing import Any, Dict, List

CPU_CHOICES = [1, 2, 4, 6, 8, 12, 16, 24, 32]
MEMORY_CHOICES_GB = [2, 4, 8, 16, 32, 48, 64, 96, 128]
GPU_MEMORY_CHOICES_GB = [2, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96]

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
    """CPU cores, memory in GB and GPUs a server on this node can be given: every GPU of the node."""
    cap = node["capacity"]
    return {
        "cpu_cores": int(cap["cpu"]),
        "memory_gb": int(gb(cap["memory"])),
        "gpus": int(cap["physical_gpu"]),
    }


def gpu_memory_gb(node: Dict[str, Any]) -> int:
    """The memory of one GPU of the node, in GB; 0 on a node without one. A GPU that takes its memory from the
    system's ("unified") has the node's memory."""
    details = node.get("gpu_details") or []
    if not details:
        return 0
    total = details[0]["memory_total"]
    if total == "unified":
        return int(gb(node["capacity"]["memory"]))
    return int(total.split()[0]) // 1024


def node_choices(node: Dict[str, Any]) -> Dict[str, List[int]]:
    capacity = node_capacity(node)
    return {
        "cpu_cores": [c for c in CPU_CHOICES if c <= capacity["cpu_cores"]] or [CPU_CHOICES[0]],
        "memory_gb": [m for m in MEMORY_CHOICES_GB if m <= capacity["memory_gb"]] or [MEMORY_CHOICES_GB[0]],
        "gpus": list(range(0, capacity["gpus"] + 1)),
        # Less than a GPU's memory: the Scheduler keeps a part of each GPU free, and the whole GPU is asked for
        # by saying no memory
        "gpu_memory_gb": [m for m in GPU_MEMORY_CHOICES_GB if m < gpu_memory_gb(node)],
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
