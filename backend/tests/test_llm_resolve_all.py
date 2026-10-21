# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""resolve_all(): in-memory bulk resolution for the proxy snapshot (SP-tgnbej SL-2)."""

import app.services.llm_backend_discovery as disc_mod
from app.api.llm.schemas import BackendEntry, ModelEntry, ModelState
from app.services.llm_model_registry import LLMModelRegistry


def test_resolve_all_returns_only_resolvable_models_in_memory(monkeypatch):
    reg = LLMModelRegistry()
    reg._models = {
        "m-ok": ModelEntry(
            id="m-ok", name="ok", server_type=["vllm"], serving_name="m-ok",
            state=ModelState.available, backend_id="vllm-x",
        ),
        # deployable, not currently served -> must be excluded from the snapshot
        "m-dep": ModelEntry(
            id="m-dep", name="dep", server_type=["vllm"], state=ModelState.deployable,
        ),
    }

    healthy = BackendEntry(
        id="vllm-x", name="vllm-x", url="http://vllm:8000", type="vllm",
        api_path="/v1", status="healthy", models=["m-ok"],
    )
    monkeypatch.setattr(
        disc_mod.llm_backend_discovery, "get_backend",
        lambda bid: healthy if bid == "vllm-x" else None,
    )
    monkeypatch.setattr(
        disc_mod.llm_backend_discovery, "get_backends_serving",
        lambda mid: [healthy] if mid == "m-ok" else [],
    )

    # AC5: the bulk path must not touch Kubernetes. Make any k8s client use blow up.
    import kubernetes
    def _boom(*_a, **_k):
        raise AssertionError("resolve_all must not call Kubernetes")
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", _boom)

    out = reg.resolve_all()

    assert {r.model_id for r in out} == {"m-ok"}  # deployable/no-backend excluded
    r = out[0]
    assert r.backend_url == "http://vllm:8000"
    assert r.api_path == "/v1"
    assert r.serving_name == "m-ok"
    assert r.error is None


def test_a_backend_with_the_messages_api_is_resolved_as_such(monkeypatch, llm_backend_types_installed):
    from app.services.llm_backend_types import BackendType

    vllm = llm_backend_types_installed["vllm"]
    llm_backend_types_installed["flare"] = BackendType(**{**vllm.__dict__, "type": "flare", "anthropic_messages": True})
    reg = LLMModelRegistry()
    reg._models = {"m": ModelEntry(id="m", name="m", server_type=["flare"], serving_name="m",
                                   state=ModelState.available, backend_id="flare-x/m")}
    flare = BackendEntry(id="flare-x/m", name="f", url="http://f:8000", type="flare",
                         api_path="/v1", status="healthy", models=["m"])
    monkeypatch.setattr(disc_mod.llm_backend_discovery, "get_backend", lambda bid: flare)

    assert reg.resolve("m").anthropic_messages is True


def test_a_catalog_alias_resolves_to_its_model_and_is_in_the_answer(monkeypatch):
    reg = LLMModelRegistry()
    reg._models = {"org/fast": ModelEntry(id="org/fast", name="Fast", server_type=["vllm"], serving_name="org/fast",
                                          state=ModelState.available, backend_id="vllm-x", aliases=["thinkube-fast"])}
    healthy = BackendEntry(id="vllm-x", name="vllm-x", url="http://vllm:8000", type="vllm",
                           api_path="/v1", status="healthy", models=["org/fast"])
    monkeypatch.setattr(disc_mod.llm_backend_discovery, "get_backend", lambda bid: healthy)

    r = reg.resolve("thinkube-fast")
    assert r.model_id == "org/fast" and r.aliases == ["thinkube-fast"]
    assert reg.resolve_all()[0].aliases == ["thinkube-fast"]


def test_part_of_a_model_id_names_no_model():
    reg = LLMModelRegistry()
    reg._models = {"org/Qwen3-8B": ModelEntry(id="org/Qwen3-8B", name="Q", server_type=["vllm"])}
    assert reg._resolve_alias("Qwen3") is None
    assert reg._resolve_alias("qwen3-8b").id == "org/Qwen3-8B"
