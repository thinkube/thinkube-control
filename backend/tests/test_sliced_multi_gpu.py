#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A model larger than one GPU of a node that shares its GPUs in time slices.

On such a node a request for several slices can be served by slices of one
GPU. A load that needs more than one GPU takes every GPU of the node, every
slice, so its tensor-parallel ranks get GPUs of their own, as a notebook
server on every GPU does.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.api.llm.schemas import GPUAllocation, GPUNode
from app.services import llm_gpu_tracker as tracker_module
from app.services.llm_gpu_tracker import LLMGPUTracker, gpu_slots
from app.services.llm_lifecycle import LLMLifecycleManager
from app.services.llm_pod_manager import deployment_gpu_request


def _sliced(**kw) -> GPUNode:
    base = dict(
        name="tkamd2", gpu_count=2, gpu_replicas=2, total_slots=4, available_slots=4,
        total_memory_gb=48.0, per_gpu_memory_gb=24.0, per_gpu_vram_gb=24.0,
        used_memory_gb=0.0, arch="discrete", shared_memory=True,
    )
    base.update(kw)
    return GPUNode(**base)


@pytest.fixture
def tracker(monkeypatch):
    t = LLMGPUTracker.__new__(LLMGPUTracker)
    t._memory_threshold = 0.85
    t._allocations = {}
    t._gpu_nodes = {"tkamd2": _sliced()}

    async def no_metrics(node_name):
        return None

    async def total(node, metrics):
        return 48.0, False

    monkeypatch.setattr(t, "fetch_node_metrics", no_metrics)
    monkeypatch.setattr(t, "_effective_total_gb", total)
    return t


def test_one_gpu_is_one_slice():
    assert gpu_slots(_sliced(), 1) == 1


def test_every_gpu_of_a_sliced_node_is_every_slice():
    assert gpu_slots(_sliced(), 2) == 4


def test_part_of_the_gpus_of_a_sliced_node_is_refused():
    with pytest.raises(ValueError, match="one GPU or all 3"):
        gpu_slots(_sliced(gpu_count=3, total_slots=6), 2)


def test_a_node_without_slices_gives_the_gpus_asked_for():
    assert gpu_slots(_sliced(gpu_replicas=1, total_slots=2, shared_memory=False), 2) == 2


def test_a_model_on_both_gpus_fits_a_free_node(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 29.7, 2, weight_gb=24.0))
    assert plan["fits"] is True
    assert plan["tensor_parallel_size"] == 2


def test_a_model_on_both_gpus_waits_for_every_slice(tracker):
    tracker._allocations["tkamd2"] = [GPUAllocation(
        model_id="BSC-LT/salamandraTA-2b-instruct", backend_id="vllm-tkamd2/x",
        node_name="tkamd2", estimated_memory_gb=6.0, slots=1,
    )]
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 29.7, 2, weight_gb=24.0))
    assert plan["fits"] is False
    assert "3 free time-slice slot(s) of 4" in plan["reason"]
    assert "needs 4" in plan["reason"]


def test_more_gpus_than_the_node_has_is_refused(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 60.0, 3, weight_gb=55.0))
    assert plan["fits"] is False
    assert "3 GPUs" in plan["reason"]


def test_a_model_larger_than_one_gpu_of_a_sliced_node_gets_every_gpu(tracker, monkeypatch):
    monkeypatch.setattr(tracker_module, "llm_gpu_tracker", tracker)
    mgr = LLMLifecycleManager()
    assert mgr._gpus_needed(29.7, "tkamd2") == 2
    assert mgr._gpus_needed(10.0, "tkamd2") == 1


def test_the_gpu_request_of_a_deployment_sums_its_containers():
    def container(gpus):
        limits = {"nvidia.com/gpu": str(gpus)} if gpus else {}
        return SimpleNamespace(resources=SimpleNamespace(limits=limits))

    deployment = SimpleNamespace(spec=SimpleNamespace(template=SimpleNamespace(
        spec=SimpleNamespace(containers=[container(4), container(0)]))))
    assert deployment_gpu_request(deployment) == 4
