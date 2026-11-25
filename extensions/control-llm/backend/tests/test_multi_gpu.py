#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A model larger than one GPU of a node of separate GPUs: it is planned on as many of the node's GPUs as its
memory needs, never more than the node has. Whether the GPUs are free is the Scheduler's to say, at the load."""

import asyncio
from types import SimpleNamespace

import pytest

from app.api.llm.schemas import GPUAllocation, GPUNode
from app.services import llm_gpu_tracker as tracker_module
from app.services.llm_gpu_tracker import LLMGPUTracker
from app.services.llm_lifecycle import LLMLifecycleManager
from app.services.llm_pod_manager import deployment_gpu_request


def _node(**kw) -> GPUNode:
    base = dict(
        name="tkamd2", gpu_count=2, gpu_replicas=1, total_slots=2, available_slots=2,
        total_memory_gb=48.0, per_gpu_memory_gb=24.0, per_gpu_vram_gb=24.0,
        used_memory_gb=0.0, arch="discrete", shared_memory=False,
    )
    base.update(kw)
    return GPUNode(**base)


@pytest.fixture
def tracker(monkeypatch):
    t = LLMGPUTracker.__new__(LLMGPUTracker)
    t._memory_threshold = 0.85
    t._allocations = {}
    t._gpu_nodes = {"tkamd2": _node()}

    async def no_metrics(node_name):
        return None

    async def total(node, metrics):
        return 48.0, False

    monkeypatch.setattr(t, "fetch_node_metrics", no_metrics)
    monkeypatch.setattr(t, "_effective_total_gb", total)
    return t


def test_a_model_on_both_gpus_fits_a_node_of_two(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 29.7, 2, weight_gb=24.0))
    assert plan["fits"] is True and plan["tensor_parallel_size"] == 2


def test_models_on_a_node_are_not_refused_by_a_count_of_slices(tracker):
    # Three models of 3 GB on a node of two GPUs: more models than GPUs, and the memory left decides, the Scheduler
    # whether the GPUs are free
    tracker._allocations["tkamd2"] = [GPUAllocation(
        model_id=f"m{i}", backend_id=f"vllm-tkamd2/m{i}", node_name="tkamd2", estimated_memory_gb=3.0, slots=1,
    ) for i in range(3)]
    assert asyncio.run(tracker.plan_sizing("tkamd2", 6.0, 1, weight_gb=4.0))["fits"] is True
    assert "free_slots" not in asyncio.run(tracker.plan_sizing("tkamd2", 6.0, 1, weight_gb=4.0))


def test_more_gpus_than_the_node_has_is_refused(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 60.0, 3, weight_gb=55.0))
    assert plan["fits"] is False and "3 GPUs" in plan["reason"]


def test_a_model_larger_than_one_gpu_gets_as_many_as_its_memory_needs(tracker, monkeypatch):
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


def test_a_load_on_the_gpus_the_caller_chose_is_not_refused_by_the_estimate(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkamd2", 29.7, 1, weight_gb=21.7, estimate_binds=False))
    assert (plan["fits"], plan["estimate_fits"], plan["tensor_parallel_size"]) == (True, False, 1)
    assert plan["gpu_memory_utilization"] == 0.95
    assert asyncio.run(tracker.plan_sizing("tkamd2", 29.7, 1, weight_gb=21.7))["fits"] is False
