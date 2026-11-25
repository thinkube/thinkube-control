# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A load in flight is cancelled by an unload, a node whose taints the backend's pods do not tolerate refuses a load,
and a model unloaded from a backend is not taken for loaded there while discovery still lists its stopping pod."""

import asyncio
from types import SimpleNamespace

import pytest

from app.api.llm.schemas import ModelEntry, ModelState
from app.services.llm_lifecycle import LLMLifecycleManager
from app.services.llm_model_registry import LLMModelRegistry
from app.services.llm_pod_manager import untolerated_taint

MODEL = "google/gemma-4-31B-it-qat-w4a16-ct"
BACKEND = "vllm-tkamd2/google-gemma-4-31b-it-qat-w4a16-ct"
TRAINING = {"key": "thinkube.io/training", "value": "true", "effect": "NoSchedule"}


def test_a_taint_without_a_matching_toleration_keeps_the_pods_off():
    assert untolerated_taint([], [TRAINING]) == "thinkube.io/training=true:NoSchedule"
    assert untolerated_taint([{"key": "thinkube.io/training", "operator": "Exists"}], [TRAINING]) is None
    assert untolerated_taint([{"operator": "Exists"}], [TRAINING]) is None
    assert untolerated_taint([{"key": "thinkube.io/training", "value": "false"}], [TRAINING]) is not None
    assert untolerated_taint([{"key": "thinkube.io/training", "value": "true", "effect": "NoExecute"}],
                             [TRAINING]) is not None
    assert untolerated_taint([], [{"key": "x", "effect": "PreferNoSchedule"}]) is None
    assert untolerated_taint(None, None) is None


@pytest.fixture
def world(monkeypatch):
    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    entry = ModelEntry(id=MODEL, name="31B", server_type=["vllm"], serving_name=MODEL,
                       state=ModelState.loading, backend_id=BACKEND)
    registry._models = {MODEL: entry}

    import app.services.llm_backend_discovery as disc
    import app.services.llm_gpu_tracker as trk
    import app.services.llm_lifecycle as life
    import app.services.llm_model_registry as reg
    import app.services.llm_pod_manager as pods

    listed = {"backends": []}
    monkeypatch.setattr(disc, "llm_backend_discovery", SimpleNamespace(list_backends=lambda: listed["backends"]))
    released = []
    allocated = []
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(
        release_allocation=released.append, get_eviction_candidates=lambda: [],
        memory_type=lambda node: "discrete",
        record_allocation=lambda model_id, *a, **k: allocated.append(model_id)))
    monkeypatch.setattr(reg, "llm_model_registry", registry)
    monkeypatch.setattr(life, "llm_lifecycle", SimpleNamespace(planned_memory_gb=lambda model_id: 24.0))
    deleted = []

    async def delete_pod(backend_type, node, model_id=None):
        deleted.append((backend_type, node, model_id))
        return True

    monkeypatch.setattr(pods, "llm_pod_manager", SimpleNamespace(
        delete_pod=delete_pod, check_pod_status=lambda t, n, m=None: ("progressing", ""),
        scale_to_zero=lambda t, n, m=None: None, gpu_slots_of=lambda t, n, m=None: 1))
    return SimpleNamespace(registry=registry, entry=entry, deleted=deleted, released=released, listed=listed)


def serving(world):
    """Discovery lists the model on its backend, as it does while the pod stops."""
    world.listed["backends"] = [SimpleNamespace(id=BACKEND, type="vllm", status="healthy", models=[MODEL],
                                                model_context={MODEL: 8192})]


def test_an_unload_of_a_loading_model_deletes_its_deployment(world):
    answer = asyncio.run(LLMLifecycleManager().unload_model(MODEL))
    assert answer.state == ModelState.deployable and "Load cancelled" in answer.message
    assert world.deleted == [("vllm", "tkamd2", MODEL)] and world.released == [MODEL]
    assert world.entry.state == ModelState.deployable and world.entry.last_error == "the load was cancelled"


def test_an_unloaded_model_is_not_loaded_again_while_its_pod_stops(world):
    asyncio.run(LLMLifecycleManager().unload_model(MODEL))
    serving(world)
    world.registry._reconcile_states()
    assert world.entry.state == ModelState.deployable
    # Once discovery no longer lists it there, a pod serving it is taken for loaded again
    world.listed["backends"] = []
    world.registry._reconcile_states()
    serving(world)
    world.registry._reconcile_states()
    assert world.entry.state == ModelState.available and world.entry.backend_id == BACKEND


def test_a_load_cancelled_before_its_deployment_existed_deletes_it_once_made(world, monkeypatch):
    import app.services.llm_pod_manager as pods

    made = []

    async def ensure_pod(backend_type, node, **kwargs):
        made.append((backend_type, node, kwargs["model_id"]))
        return True, None

    pods.llm_pod_manager.ensure_pod = ensure_pod
    manager = LLMLifecycleManager()
    asyncio.run(manager.unload_model(MODEL))
    asyncio.run(manager._load_performance_background(MODEL, "vllm", "tkamd2", 1,
                                                     {"model_id": MODEL, "task": "text-generation"}, 24.0))
    assert made == [("vllm", "tkamd2", MODEL)]
    assert world.deleted == [("vllm", "tkamd2", MODEL), ("vllm", "tkamd2", MODEL)]
    assert world.entry.state == ModelState.deployable
