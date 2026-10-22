# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A model listed for Ollama and for a one-model engine has a name on each."""

import app.services.llm_backend_discovery as disc_mod
from app.api.llm.schemas import BackendEntry, ModelEntry, ModelState
from app.services.llm_model_registry import LLMModelRegistry, served_name


def _gguf(state, backend_id=None):
    return ModelEntry(
        id="org/Model-GGUF", name="Model", server_type=["ollama", "vllm"],
        serving_name="model", state=state, backend_id=backend_id,
    )


def test_ollama_serves_the_catalog_name_and_a_one_model_pod_the_id(llm_backend_types_installed):
    entry = _gguf(ModelState.deployable)
    assert served_name(entry, "ollama") == "model"
    assert served_name(entry, "vllm") == "org/Model-GGUF"


def test_a_one_model_pod_serving_the_id_makes_the_model_available(monkeypatch, llm_backend_types_installed):
    reg = LLMModelRegistry()
    reg._models = {"org/Model-GGUF": _gguf(ModelState.loading, "vllm-n/org-model-gguf")}
    pod = BackendEntry(
        id="vllm-n/org-model-gguf", name="p", url="http://p:8000", type="vllm",
        api_path="/v1", status="healthy", models=["org/Model-GGUF"],
    )
    monkeypatch.setattr(disc_mod.llm_backend_discovery, "list_backends", lambda: [pod])
    monkeypatch.setattr(reg, "_sync_gpu_allocation", lambda *a, **k: None)

    reg._reconcile_states()

    entry = reg.get_model("org/Model-GGUF")
    assert entry.state == ModelState.available
    assert entry.backend_id == "vllm-n/org-model-gguf"


def test_resolve_names_the_model_as_the_backend_serves_it(monkeypatch, llm_backend_types_installed):
    reg = LLMModelRegistry()
    reg._models = {"org/Model-GGUF": _gguf(ModelState.available, "vllm-n/org-model-gguf")}
    pod = BackendEntry(
        id="vllm-n/org-model-gguf", name="p", url="http://p:8000", type="vllm",
        api_path="/v1", status="healthy", models=["org/Model-GGUF"],
    )
    monkeypatch.setattr(disc_mod.llm_backend_discovery, "get_backend", lambda bid: pod)

    assert reg.resolve("org/Model-GGUF").serving_name == "org/Model-GGUF"
