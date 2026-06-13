#!/usr/bin/env python3
"""Sizing tests for LLMLifecycleManager._estimate_memory (SP-tgkxea, SL-1).

Guards the multimodal under-count crashloop: for unsloth/Qwen3.6-27B-NVFP4 the
params×dtype estimate (27B × 0.5 = ~13.5 GB) is far below the real 24.57 GiB
checkpoint (vision encoder + non-quantized embeddings/lm_head). At a small
context that under-count drove gpu_memory_utilization (0.13) below the weight
size, so vLLM crashlooped on KV-cache allocation. _estimate_memory must prefer
a measured weight_bytes over the heuristic when one is present.
"""

from app.services.llm_lifecycle import LLMLifecycleManager
from app.services.llm_gpu_tracker import _floored_util, FRAMEWORK_OVERHEAD_GB
from app.api.llm.schemas import ModelEntry

GIB = 1024 ** 3
TOTAL_GB = 122.0  # ~GB10 unified memory
_mgr = LLMLifecycleManager()


def _entry(**kw) -> ModelEntry:
    base = dict(
        id="unsloth/Qwen3.6-27B-NVFP4", name="Qwen 3.6 27B NVFP4",
        server_type=["vllm"], task="text-generation",
        params_b=27.0, quantization="NVFP4", context_length=262144,
    )
    base.update(kw)
    return ModelEntry(**base)


def test_measured_weight_bytes_preferred_over_params_heuristic():
    # Real checkpoint 24.57 GiB vs params heuristic ~13.5 GB.
    measured = _mgr._estimate_memory(
        _entry(weight_bytes=int(24.57 * GIB)), max_context_length=8192
    )
    heuristic = _mgr._estimate_memory(_entry(), max_context_length=8192)

    # The measured estimate must reflect the real ~24.57 GiB weights ...
    assert measured >= 24.0
    # ... the heuristic-only estimate stays near the ~13.5 GB under-count ...
    assert heuristic < 19.0
    # ... and the ~11 GB weight difference shows through (proves measured wins).
    assert measured - heuristic > 8.0


def test_params_heuristic_used_when_no_measured_size():
    # No weight_bytes -> fall back to params×bpp (27B NVFP4 -> ~13.5 GB + KV + 1).
    est = _mgr._estimate_memory(_entry(), max_context_length=8192)
    assert 13.0 < est < 19.0


# --- SL-2: the gpu_memory_utilization floor (regression-proofing) ---

def test_floor_raises_util_to_fit_weights_at_small_context():
    # The crash case: small-context computed util 0.13 on a 24.57 GiB model.
    util = _floored_util(0.13, 24.57, TOTAL_GB)
    assert util > 0.13  # raised
    # Budget must now cover weights + framework overhead.
    assert util * TOTAL_GB >= 24.57 + FRAMEWORK_OVERHEAD_GB - 0.01


def test_floor_never_lowers_a_comfortable_util():
    # A model whose computed util already fits is unchanged (no over/under-ride).
    assert _floored_util(0.6, 24.57, TOTAL_GB) == 0.6


def test_floor_capped_at_0_95():
    # Weights+overhead beyond 95% of the device still cap at 0.95, never above.
    assert _floored_util(0.1, 200.0, TOTAL_GB) == 0.95


def test_floor_noop_without_weight_or_capacity():
    # Callers that don't pass a weight (or unknown capacity) are unaffected.
    assert _floored_util(0.13, None, TOTAL_GB) == 0.13
    assert _floored_util(0.13, 24.57, 0) == 0.13
