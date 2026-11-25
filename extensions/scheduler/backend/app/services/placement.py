# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Where a job runs: the GPUs of its node it is given, or what it waits for.

A job asks for a number of GPUs and says the memory it takes on each; a job that says none has its GPUs alone.
On a node with separate GPUs a GPU takes a job when its memory holds it beside what is on it already: the
scheduler's own jobs, by the memory they were given, and the work the scheduler did not start, by what the GPU
reports in use beyond that. On a node whose GPU shares the system's memory a job fits when the node's available
memory holds its memory and its GPU memory together.

MARGIN of a GPU's memory is kept free, since a GPU does not keep one job's memory from another's.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.models.jobs import HOLDING, Job
from app.services.nodes import NONE, SEPARATE, SHARED

GIB = 2**30
MARGIN = 0.05
# What a GPU that holds no work reports in use
IDLE_BYTES = 256 * 2**20
# A job's memory is not in its node's figures until it has loaded: for this long after its start, a shared node's
# available memory is counted less what the job was given
LOADING = timedelta(minutes=10)


class CannotRun(Exception):
    """The job can never run on its node as it is; the message says why."""


def gib(n: float) -> str:
    return f"{n / GIB:.1f} GiB"


def holders(jobs: List[Job], node: str) -> List[Job]:
    """The scheduler's jobs that hold GPUs of the node."""
    return [j for j in jobs if j.node == node and j.status in HOLDING and j.gpu_ids]


def _separate(job: Job, node: Dict[str, Any], held: List[Job]) -> Tuple[Optional[List[str]], Optional[str]]:
    gpus = node["gpus"]
    if job.gpus > len(gpus):
        raise CannotRun(f"{node['name']} has {len(gpus)} GPUs, and the job asks for {job.gpus}")
    need = None if job.gpu_memory_gb is None else job.gpu_memory_gb * GIB
    fitting, full = [], []
    for gpu in gpus:
        if need is not None and need > gpu["memory_total_bytes"] * (1 - MARGIN):
            raise CannotRun(f"the job takes {gib(need)} on a GPU, and GPU {gpu['index']} of {node['name']} has "
                            f"{gib(gpu['memory_total_bytes'])}, of which {MARGIN:.0%} is kept free")
        on_it = [j for j in held if gpu["id"] in j.gpu_ids]
        alone = next((j for j in on_it if j.gpu_memory_gb is None), None)
        given = sum(j.gpu_memory_gb * GIB for j in on_it if j.gpu_memory_gb is not None)
        outside = max(0, gpu["memory_used_bytes"] - given) if alone is None else 0
        if alone is not None:
            full.append(f"GPU {gpu['index']} is job {alone.name}'s alone")
        elif need is None:
            if on_it:
                full.append(f"GPU {gpu['index']} holds job {on_it[0].name}")
            elif outside > IDLE_BYTES:
                full.append(f"GPU {gpu['index']} holds {gib(outside)} of work the scheduler did not start")
            else:
                fitting.append((0, gpu["index"], gpu["id"]))
        else:
            free = gpu["memory_total_bytes"] * (1 - MARGIN) - given - outside
            if need <= free:
                # The fullest GPU that holds the job first, which keeps the emptier ones for larger work
                fitting.append((free, gpu["index"], gpu["id"]))
            else:
                full.append(f"GPU {gpu['index']} has {gib(max(0, free))} free")
    if len(fitting) >= job.gpus:
        return [gpu_id for _, _, gpu_id in sorted(fitting)[:job.gpus]], None
    what = "a GPU alone" if need is None else f"{gib(need)} on a GPU"
    count = f"{job.gpus} GPUs" if job.gpus > 1 else "a GPU"
    return None, f"waiting for {count} of {node['name']} ({what}): " + "; ".join(full)


def _shared(job: Job, node: Dict[str, Any], held: List[Job], now: datetime) -> Tuple[Optional[List[str]],
                                                                                    Optional[str]]:
    if job.gpus > 1:
        raise CannotRun(f"{node['name']} has one GPU, and the job asks for {job.gpus}")
    if job.gpu_memory_gb is None:
        if held:
            return None, f"waiting for the GPU of {node['name']} alone: it holds job {held[0].name}"
        need = job.memory_gb * GIB
    else:
        alone = next((j for j in held if j.gpu_memory_gb is None), None)
        if alone is not None:
            return None, f"waiting for the GPU of {node['name']}: it is job {alone.name}'s alone"
        need = (job.memory_gb + job.gpu_memory_gb) * GIB
    loading = sum((j.memory_gb + (j.gpu_memory_gb or 0)) * GIB for j in held
                  if j.started_at is not None and now - j.started_at < LOADING)
    free = node["memory_available_bytes"] - loading
    if need > free:
        return None, (f"waiting for {gib(need)} of memory on {node['name']}, whose GPU takes its memory from the "
                      f"system's: {gib(max(0, free))} is available")
    return [node["gpus"][0]["id"]], None


def place(job: Job, node: Dict[str, Any], jobs: List[Job], now: datetime) -> Tuple[Optional[List[str]], Optional[str]]:
    """(the ids of the GPUs the job is given, None), or (None, what it waits for). A job that asks for no GPU is
    given none and waits for nothing here: Kubernetes places it by its memory and CPU. CannotRun when the node can
    never run it."""
    if job.gpus == 0:
        return [], None
    if node.get("error"):
        return None, f"waiting for {node['name']} to be read: {node['error']}"
    held = [j for j in holders(jobs, node["name"]) if j.id != job.id]
    if node["kind"] == NONE:
        raise CannotRun(f"{node['name']} has no GPU, and the job asks for {job.gpus}")
    if node["kind"] == SEPARATE:
        return _separate(job, node, held)
    if node["kind"] == SHARED:
        return _shared(job, node, held, now)
    raise ValueError(f"a node's kind is {SEPARATE!r}, {SHARED!r} or {NONE!r}, not {node['kind']!r}")
