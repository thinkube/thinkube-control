# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A model that discovery stops seeing is downgraded only when its pod is down.

Discovery can miss a serving model; the pod is asked before the model is
downgraded and its deployment scaled to zero.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.llm.schemas import ModelEntry, ModelState
from app.services.llm_backend_discovery import LLMBackendDiscovery
from app.services.llm_model_registry import LLMModelRegistry

MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"


@pytest.fixture
def world(monkeypatch):
    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    entry = ModelEntry(id=MODEL, name="35B", server_type=["vllm"], serving_name=MODEL,
                       state=ModelState.available,
                       backend_id="vllm-tkspark/nvidia-qwen3-6-35b-a3b-nvfp4")
    entry._last_available_at = datetime.utcnow() - timedelta(seconds=200)
    registry._models = {MODEL: entry}

    import app.services.llm_backend_discovery as disc
    import app.services.llm_gpu_tracker as trk
    import app.services.llm_pod_manager as pods

    monkeypatch.setattr(disc, "llm_backend_discovery", SimpleNamespace(list_backends=lambda: []))
    released = []
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(release_allocation=released.append))
    scaled, state = [], {"pod": "ready"}
    monkeypatch.setattr(pods, "llm_pod_manager", SimpleNamespace(
        check_pod_status=lambda t, n, m=None: (state["pod"], ""),
        scale_to_zero=lambda t, n, m=None: scaled.append((t, n, m)),
    ))
    return SimpleNamespace(registry=registry, entry=entry, scaled=scaled, state=state)


@pytest.mark.parametrize("pod", ["ready", "unknown"])
def test_a_model_whose_pod_is_ready_or_unreadable_is_kept(world, pod):
    world.state["pod"] = pod
    world.registry._reconcile_states()
    assert world.entry.state == ModelState.available
    assert world.scaled == []


@pytest.mark.parametrize("pod", ["progressing", "failed", "absent"])
def test_a_model_whose_pod_is_down_is_downgraded_and_scaled_to_zero(world, pod):
    world.state["pod"] = pod
    world.registry._reconcile_states()
    assert world.entry.state == ModelState.deployable
    assert world.scaled == [("vllm", "tkspark", MODEL)]


def test_discovery_keeps_the_status_and_models_of_a_known_pod():
    d = LLMBackendDiscovery.__new__(LLMBackendDiscovery)
    d._backends, d._probe_failures = {}, {}
    from app.api.llm.schemas import BackendEntry

    d._backends["vllm-tkspark/m"] = BackendEntry(
        id="vllm-tkspark/m", name="vLLM", url="http://10.0.0.1:7860", type="vllm",
        api_path="/v1", status="healthy", models=[MODEL], node="tkspark",
    )
    d._static_backends_raw = "- name: vllm-tkspark/m\n  url: http://10.0.0.1:7860\n  type: vllm\n"
    d._discover_static()
    assert d._backends["vllm-tkspark/m"].status == "healthy"
    assert d._backends["vllm-tkspark/m"].models == [MODEL]
