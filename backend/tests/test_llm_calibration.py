# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0
"""Sizing a load from a model's calibration (thinkube-metadata models.json)."""

from types import SimpleNamespace

import pytest

import app.services.llm_gpu_tracker as trk
from app.services.llm_gpu_tracker import _share_util
from app.services.llm_lifecycle import LLMLifecycleManager

GIB = 1024 ** 3

# Qwen3.6-35B-A3B-NVFP4 as measured on the DGX Spark.
CAL_35B = {
    "uma": {
        "context": 32768,
        "weight_bytes": 21872120955,
        "kv_bytes_per_token": 23122,
        "overhead_bytes": 7581940777,
        "node_delta_bytes": 53687091200,
        "measured_with": {"share": 0.38, "kv_tokens": 873501},
    }
}


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(is_uma=lambda n: n == "tkspark"))
    return LLMLifecycleManager()


def test_the_share_is_weights_overhead_and_the_context_cache(mgr):
    share, node = mgr._calibrated_memory(SimpleNamespace(calibration=CAL_35B), 32768, "tkspark")
    expected = (21872120955 + 7581940777 + 32768 * 23122) / GIB
    assert share == pytest.approx(expected, abs=0.01)


def test_on_unified_memory_the_node_also_gives_what_the_process_holds_outside_the_share(mgr):
    share, node = mgr._calibrated_memory(SimpleNamespace(calibration=CAL_35B), 32768, "tkspark")
    measured_share = 21872120955 + 7581940777 + 873501 * 23122
    outside = (53687091200 - measured_share) / GIB
    assert node == pytest.approx(share + outside, abs=0.01)
    assert 3.0 < outside < 4.5


def test_a_model_without_a_calibration_for_the_memory_type_has_none(mgr):
    assert mgr._calibrated_memory(SimpleNamespace(calibration=CAL_35B), 32768, "tkamd2") is None
    assert mgr._calibrated_memory(SimpleNamespace(calibration=None), 32768, "tkspark") is None


def test_the_memory_share_is_rounded_up():
    assert _share_util(28.14, 121.7) == 0.24
    assert _share_util(13.73, 121.7) == 0.12


def test_a_share_beyond_95_percent_is_refused():
    with pytest.raises(ValueError):
        _share_util(120.0, 121.7)
