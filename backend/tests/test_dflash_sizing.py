#!/usr/bin/env python3
"""DFlash drafter sizing + config-injection tests (SP-tgkm1m, SL-2).

DFlash speculative decoding loads a *separate* drafter model into the target's
vLLM process, so (a) sizing must add the drafter's weight on top of the target,
and (b) the catalog's drafter *id* must be rewritten to its resolved MLflow path
before reaching vLLM. These tests pin the pure pieces; the live load/benchmark is
the slice's end-to-end verification.
"""

import json

from app.services.llm_lifecycle import LLMLifecycleManager
from app.api.llm.schemas import ModelEntry

GIB = 1024 ** 3
_mgr = LLMLifecycleManager()

DFLASH = '{"method": "dflash", "model": "z-lab/Qwen3.6-27B-DFlash"}'
MTP = '{"method": "mtp", "num_speculative_tokens": 1}'


def _target(**kw) -> ModelEntry:
    base = dict(
        id="unsloth/Qwen3.6-27B-NVFP4", name="t", server_type=["vllm"],
        params_b=27.0, quantization="NVFP4", context_length=262144,
        weight_bytes=int(24.57 * GIB),
    )
    base.update(kw)
    return ModelEntry(**base)


# --- drafter reference parsing ---

def test_dflash_drafter_id_parsed():
    assert _mgr._dflash_drafter_id(DFLASH) == "z-lab/Qwen3.6-27B-DFlash"


def test_mtp_config_has_no_drafter():
    assert _mgr._dflash_drafter_id(MTP) is None


def test_already_resolved_path_is_not_re_resolved():
    cfg = '{"method": "dflash", "model": "/mlflow-models/artifacts/1/abc/artifacts/model"}'
    assert _mgr._dflash_drafter_id(cfg) is None


def test_no_spec_config():
    assert _mgr._dflash_drafter_id(None) is None


# --- path injection ---

def test_inject_drafter_path_swaps_id_for_path():
    out = _mgr._inject_drafter_path(DFLASH, "/mlflow-models/artifacts/1/run/artifacts/model")
    parsed = json.loads(out)
    assert parsed["method"] == "dflash"
    assert parsed["model"] == "/mlflow-models/artifacts/1/run/artifacts/model"


# --- sizing accounts for the drafter weight (AC4) ---

def test_drafter_weight_added_to_estimate():
    base = _mgr._estimate_memory(_target(), max_context_length=8192)
    with_drafter = _mgr._estimate_memory(_target(), max_context_length=8192, extra_weight_gb=4.0)
    assert round(with_drafter - base, 1) == 4.0
    # The combined footprint must exceed the ~24.57 GiB target weights alone.
    assert with_drafter > 24.57


def test_no_drafter_estimate_unchanged():
    # Regression (AC6): a model without a drafter sizes exactly as before.
    assert _mgr._estimate_memory(_target(), 8192, extra_weight_gb=0.0) == \
        _mgr._estimate_memory(_target(), 8192)


# --- per-load num_speculative_tokens override (SL-4) ---

def test_set_num_speculative_tokens_override():
    p = json.loads(_mgr._set_num_speculative_tokens(DFLASH, 7))
    assert p["num_speculative_tokens"] == 7
    assert p["method"] == "dflash"  # other fields preserved


def test_set_num_speculative_tokens_replaces_existing():
    cfg = '{"method": "dflash", "model": "/mlflow/x", "num_speculative_tokens": 15}'
    p = json.loads(_mgr._set_num_speculative_tokens(cfg, 4))
    assert p["num_speculative_tokens"] == 4
    assert p["model"] == "/mlflow/x"  # path override (from SL-2) untouched
