"""How load_llm_model resolves the backend, the node and the default context."""

import asyncio
from types import SimpleNamespace

import pytest

import app.services.llm_lifecycle as lifecycle_module
from app.api.llm.schemas import ModelState
from app.services.llm_lifecycle import llm_lifecycle


def entry(**overrides):
    base = dict(
        id="Qwen/Qwen3-8B", role="primary", server_type=["vllm"], state=ModelState.deployable,
        context_length=131072, params_b=8.2, active_params_b=None, quantization="BF16", size="~16GB",
        backend_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def node(name, remaining, slots=1):
    return SimpleNamespace(name=name, ai_remaining_gb=remaining, available_slots=slots)


@pytest.fixture
def world(monkeypatch):
    """A registry with one model, two discovered backends, two GPU nodes, and a sizing rule."""
    model = entry()
    backends = {
        "vllm-tkspark": SimpleNamespace(id="vllm-tkspark", type="vllm", node="tkspark"),
        "ollama-tkamd2": SimpleNamespace(id="ollama-tkamd2", type="ollama", node="tkamd2"),
    }
    state = {"fits_up_to_gb": 1000.0, "nodes": [node("tkamd2", 20.0), node("tkspark", 66.0)]}

    monkeypatch.setattr(lifecycle_module, "llm_model_registry", SimpleNamespace(get_model=lambda mid: model), raising=False)
    import app.services.llm_model_registry as reg
    monkeypatch.setattr(reg, "llm_model_registry", SimpleNamespace(get_model=lambda mid: model, update_model_state=lambda *a, **k: None))
    import app.services.llm_backend_discovery as disc
    monkeypatch.setattr(disc, "llm_backend_discovery", SimpleNamespace(
        get_backend=lambda bid: backends.get(bid), list_backends=lambda: list(backends.values())))
    import app.services.llm_gpu_tracker as trk

    async def get_status():
        return SimpleNamespace(nodes=state["nodes"])

    async def plan_sizing(node_name, target_gb, gpu_count=1, weight_gb=None):
        return {"fits": target_gb <= state["fits_up_to_gb"], "reason": "ok"}

    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(
        get_status=get_status, plan_sizing=plan_sizing, is_uma=lambda n: n == "tkspark",
        get_node=lambda n: SimpleNamespace(per_gpu_vram_gb=24.0, gpu_count=1)))
    monkeypatch.setattr(llm_lifecycle, "_gpus_needed", lambda est, n: 1)

    captured = {}

    async def fake_performance(model_id, backend=None, node=None, max_context_length=None, num_speculative_tokens=None):
        captured.update(backend=backend, node=node, context=max_context_length)
        return SimpleNamespace(model_id=model_id, state=ModelState.loading, message="captured")

    async def fake_ollama(model_id, keep_alive, node, max_context_length=None):
        captured.update(backend="ollama", node=node, context=max_context_length)
        return SimpleNamespace(model_id=model_id, state=ModelState.loading, message="captured")

    monkeypatch.setattr(llm_lifecycle, "_load_performance", fake_performance)
    monkeypatch.setattr(llm_lifecycle, "_load_ollama", fake_ollama)
    return SimpleNamespace(model=model, state=state, captured=captured)


def test_a_backend_id_names_its_type_and_node(world):
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", backend="vllm-tkspark"))
    assert world.captured["backend"] == "vllm"
    assert world.captured["node"] == "tkspark"


def test_an_unknown_backend_is_refused_with_the_accepted_forms(world):
    result = asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", backend="vllm-nowhere"))
    assert result.state == ModelState.deployable
    assert "vllm-nowhere" in result.message
    assert "vllm-tkspark" in result.message and "ollama-tkamd2" in result.message
    assert world.captured == {}


def test_without_a_node_the_node_with_most_memory_left_is_used(world):
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B"))
    assert world.captured["node"] == "tkspark"
    world.state["nodes"] = [node("tkamd2", 20.0), node("tkspark", 66.0, slots=0)]
    world.captured.clear()
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B"))
    assert world.captured["node"] == "tkamd2"


def test_the_default_context_is_the_largest_that_fits(world):
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", node="tkspark"))
    assert world.captured["context"] == 32768
    # Only the smallest estimate fits: an 8B model at 8k tokens is well under 20 GB, at 16k is not.
    world.state["fits_up_to_gb"] = llm_lifecycle._estimate_memory(world.model, 8192) + 0.1
    world.captured.clear()
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", node="tkspark"))
    assert world.captured["context"] == 8192


def test_the_model_window_caps_the_default(world):
    world.model.context_length = 4096
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", node="tkspark"))
    assert world.captured["context"] == 4096


def test_an_explicit_context_is_kept(world):
    asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3-8B", node="tkspark", max_context_length=65536))
    assert world.captured["context"] == 65536


def test_a_second_model_on_an_occupied_backend_is_refused(world, monkeypatch):
    """vLLM serves one model per pod: a load onto a backend that serves another model is refused, not ignored."""
    import app.services.llm_model_registry as reg
    other = entry(id="Qwen/Qwen3-8B", state=ModelState.available, backend_id="vllm-tkspark")
    mine = entry(id="Qwen/Qwen3.5-4B")
    models = {"Qwen/Qwen3-8B": other, "Qwen/Qwen3.5-4B": mine}
    monkeypatch.setattr(reg, "llm_model_registry", SimpleNamespace(
        get_model=lambda mid: models.get(mid), list_models=lambda: list(models.values()),
        update_model_state=lambda *a, **k: None))
    # Use the real _load_performance up to the refusal; it returns before any sizing.
    monkeypatch.setattr(llm_lifecycle, "_load_performance", type(llm_lifecycle)._load_performance.__get__(llm_lifecycle))
    result = asyncio.run(llm_lifecycle.load_model("Qwen/Qwen3.5-4B", backend="vllm-tkspark"))
    assert result.state == ModelState.deployable
    assert "vllm-tkspark already serves Qwen/Qwen3-8B" in result.message
    assert world.captured == {}


def test_reconcile_never_scales_a_shared_deployment_to_zero():
    from app.services.llm_model_registry import LLMModelRegistry

    registry = LLMModelRegistry()
    registry._models = {
        "a": entry(id="a", state=ModelState.available, backend_id="vllm-tkspark"),
        "b": entry(id="b", state=ModelState.loading, backend_id="vllm-tkspark"),
        "c": entry(id="c", state=ModelState.loading, backend_id="vllm-tkamd2"),
    }
    assert registry._others_on_backend("vllm-tkspark", "b") is True
    assert registry._others_on_backend("vllm-tkamd2", "c") is False
    assert registry._others_on_backend(None, "c") is False
