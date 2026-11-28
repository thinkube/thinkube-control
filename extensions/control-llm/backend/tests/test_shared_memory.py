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


@pytest.fixture
def placing(lifecycle, monkeypatch):
    """A shared load up to the pod it asks for: the pod manager, the tracker's sizing and the holds faked."""
    import asyncio

    from app.services.llm_pod_manager import llm_pod_manager

    calls = {"deleted": [], "ensured": []}
    MODELS["kubexlat/ja-seg"].state = "deployable"

    async def can_load(estimated, node_name):
        return True, "ok"

    async def sizing(node, target_gb, **_):
        return {"fits": True, "pod_mem_limit_gb": 30}

    async def delete_pod(backend_type, node, model_id):
        calls["deleted"].append((backend_type, node, model_id))
        return True

    async def ensure_pod(backend_type, node, **kwargs):
        calls["ensured"].append(kwargs)
        return True, None

    async def no_gpus(*a):
        return {}

    async def background(*a):
        return None

    monkeypatch.setattr(llm_gpu_tracker, "check_can_load", can_load)
    monkeypatch.setattr(llm_gpu_tracker, "plan_sizing", sizing)
    monkeypatch.setattr(llm_pod_manager, "shared_pod", lambda backend_type, node: (True, None))
    monkeypatch.setattr(llm_pod_manager, "delete_pod", delete_pod)
    monkeypatch.setattr(llm_pod_manager, "ensure_pod", ensure_pod)
    monkeypatch.setattr(llm_model_registry, "update_model_state", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_shared_pod_gpus", no_gpus)
    monkeypatch.setattr(lifecycle, "_load_shared_background", background)

    def load(uma):
        monkeypatch.setattr(llm_gpu_tracker, "memory_type", lambda node: "uma" if uma else "discrete")

        async def run():
            response = await lifecycle._load_shared("kubexlat/ja-seg", "heads", "tkspark")
            await asyncio.sleep(0)
            return response

        return asyncio.run(run())

    return load, calls


def test_a_shared_pod_that_serves_no_model_is_made_again_with_the_loads_memory_limit(placing):
    load, calls = placing
    load(uma=True)
    assert calls["deleted"] == [("heads", "tkspark", None)]
    assert calls["ensured"][0]["mem_limit_gb"] == 30


def test_a_shared_pod_that_serves_a_model_is_kept(placing):
    load, calls = placing
    llm_gpu_tracker._allocations["tkspark"] = [NS(model_id="kubexlat/ja-parse", backend_id="heads-tkspark",
                                                   estimated_memory_gb=3.0)]
    load(uma=False)
    assert calls["deleted"] == []
    assert calls["ensured"][0]["mem_limit_gb"] is None


def test_a_part_whose_base_is_held_in_4_bits_counts_the_bytes_its_entry_gives(lifecycle):
    held = NS(id="kubexlat/ja-subject-finder", base_model="FacebookAI/xlm-roberta-large", weight_bytes=GIB // 20,
              base_weight_bytes=GIB // 2)
    assert lifecycle._shared_memory(held, "tkamd2") == pytest.approx(0.5 + 0.05 + 1.0, abs=0.1)
