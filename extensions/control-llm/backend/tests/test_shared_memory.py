# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A model on a shared pod counts its base's memory only when no model on the same base is on the node yet."""

from types import SimpleNamespace as NS

import pytest

from app.services import llm_lifecycle
from app.services.llm_gpu_tracker import llm_gpu_tracker
from app.services.llm_model_registry import llm_model_registry

GIB = 1024 ** 3
MODELS = {
    "FacebookAI/xlm-roberta-large": NS(id="FacebookAI/xlm-roberta-large", base_model=None, weight_bytes=2 * GIB),
    "kubexlat/ja-seg": NS(id="kubexlat/ja-seg", base_model="FacebookAI/xlm-roberta-large", weight_bytes=GIB // 20),
    "kubexlat/ja-parse": NS(id="kubexlat/ja-parse", base_model="FacebookAI/xlm-roberta-large", weight_bytes=GIB // 16),
}


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.setattr(llm_model_registry, "get_model", lambda model_id: MODELS.get(model_id))
    monkeypatch.setattr(llm_lifecycle, "served_weight_share", lambda entry: 1.0)
    monkeypatch.setattr(llm_gpu_tracker, "_allocations", {})
    return llm_lifecycle.LLMLifecycleManager()


def test_the_first_part_on_a_node_counts_its_base_and_the_next_its_own_weights(lifecycle):
    first = lifecycle._shared_memory(MODELS["kubexlat/ja-seg"], "tkspark")
    assert first == pytest.approx(2 + 0.05 + 1.0, abs=0.1)
    llm_gpu_tracker._allocations["tkspark"] = [NS(model_id="kubexlat/ja-seg", backend_id="heads-tkspark",
                                                   estimated_memory_gb=first)]
    assert lifecycle._shared_memory(MODELS["kubexlat/ja-parse"], "tkspark") == pytest.approx(1 / 16, abs=0.01)
    # Another node holds no copy of the base yet
    assert lifecycle._shared_memory(MODELS["kubexlat/ja-parse"], "tkamd2") == pytest.approx(3.06, abs=0.1)
