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
