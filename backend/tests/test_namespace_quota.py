#!/usr/bin/env python3
"""The namespace memory quota, computed from the GPU nodes or refused."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from namespace_quota import NON_GPU_QUOTA, QuotaError, memory_quota, node_view  # noqa: E402

BUDGET = {"LLM_UMA_AI_BUDGET_GB": "96"}

# The GPU nodes of the reference cluster, as they report themselves.
RTX = node_view(
    "tkamd2",
    {"nvidia.com/gpu": "4", "memory": "122620300Ki"},
    {"nvidia.com/gpu.family": "ampere", "nvidia.com/gpu.product": "NVIDIA-GeForce-RTX-3090-SHARED"},
)
SPARK = node_view(
    "tkspark",
    {"nvidia.com/gpu": "4", "memory": "119211920Ki"},
    {"nvidia.com/gpu.family": "blackwell", "nvidia.com/gpu.product": "NVIDIA-GB10-SHARED"},
)
CPU = node_view("tkamd1", {"memory": "57396820Ki"}, {})


def test_an_application_without_gpu_work_gets_the_fixed_quota_and_reads_nothing():
    assert memory_quota(False, nodes=[], environ={}) == NON_GPU_QUOTA


def test_the_reference_cluster_is_capped_by_the_ai_budget_on_the_spark():
    assert memory_quota(True, [CPU, RTX, SPARK], BUDGET) == ("48Gi", "96Gi")


def test_a_discrete_node_alone_needs_host_overhead_only():
    assert memory_quota(True, [CPU, RTX], BUDGET) == ("8Gi", "16Gi")


def test_a_unified_memory_node_below_the_budget_uses_its_allocatable_memory():
    small = node_view(
        "small-spark",
        {"nvidia.com/gpu": "1", "memory": str(64 * 1024 * 1024) + "Ki"},
        {"nvidia.com/gpu.family": "blackwell", "nvidia.com/gpu.product": "NVIDIA-GB10"},
    )
    assert memory_quota(True, [small], BUDGET) == ("32Gi", "64Gi")


def test_gpu_work_on_a_cluster_without_gpus_is_refused():
    with pytest.raises(QuotaError, match="no node in the cluster has one"):
        memory_quota(True, [CPU], BUDGET)


def test_a_gpu_node_without_feature_labels_is_refused():
    unlabelled = node_view("gpu-x", {"nvidia.com/gpu": "1", "memory": "1000Ki"}, None)
    with pytest.raises(QuotaError, match="gpu-x"):
        memory_quota(True, [unlabelled], BUDGET)


def test_memory_not_reported_in_ki_is_refused():
    odd = node_view(
        "odd-spark",
        {"nvidia.com/gpu": "1", "memory": "120Gi"},
        {"nvidia.com/gpu.family": "blackwell", "nvidia.com/gpu.product": "NVIDIA-GB10"},
    )
    with pytest.raises(QuotaError, match="not in Ki"):
        memory_quota(True, [odd], BUDGET)


def test_a_unified_memory_node_needs_the_budget_to_be_set():
    with pytest.raises(QuotaError, match="LLM_UMA_AI_BUDGET_GB is not set"):
        memory_quota(True, [SPARK], environ={})


def test_a_node_without_allocatable_resources_is_refused():
    with pytest.raises(QuotaError, match="no allocatable"):
        node_view("broken", None, {})
