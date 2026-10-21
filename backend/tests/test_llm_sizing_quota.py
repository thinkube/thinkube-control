#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A plan on unified memory never asks for a pod limit the namespace quota refuses.

The memory quota of a GPU component's namespace is LLM_UMA_AI_BUDGET_GB at
most. A plan whose pod limit is above it would be refused by Kubernetes
with no pod to report, and the model would wait as loading until its
deadline; the plan refuses it at once and says why.
"""

import asyncio

import pytest

from app.api.llm.schemas import GPUNode
from app.services.llm_gpu_tracker import LLMGPUTracker, UMA_AI_BUDGET_GB

TOTAL_GB = 122.0  # a DGX Spark's unified memory


@pytest.fixture
def tracker(monkeypatch):
    t = LLMGPUTracker.__new__(LLMGPUTracker)
    t._memory_threshold = 0.85
    t._allocations = {}
    t._gpu_nodes = {"tkspark": GPUNode(
        name="tkspark", total_slots=4, available_slots=4, total_memory_gb=TOTAL_GB,
        used_memory_gb=0.0, arch="uma", is_uma=True,
    )}

    async def no_metrics(node_name):
        return None

    async def total(node, metrics):
        return TOTAL_GB, True

    monkeypatch.setattr(t, "fetch_node_metrics", no_metrics)
    monkeypatch.setattr(t, "_effective_total_gb", total)
    return t


def test_a_pod_limit_above_the_quota_is_refused_with_the_reason(tracker):
    # 72.6 GiB of weights: the vLLM overhead floor raises the share to 0.79.
    plan = asyncio.run(tracker.plan_sizing("tkspark", 77.1, 1, weight_gb=72.6))
    assert plan["pod_mem_limit_gb"] > UMA_AI_BUDGET_GB
    assert plan["fits"] is False
    assert "namespace quota" in plan["reason"]


def test_a_calibrated_share_under_the_quota_fits(tracker):
    plan = asyncio.run(tracker.plan_sizing("tkspark", 84.0, 1, weight_gb=74.7, share_gb=83.7))
    assert plan["pod_mem_limit_gb"] <= UMA_AI_BUDGET_GB
    assert plan["fits"] is True
