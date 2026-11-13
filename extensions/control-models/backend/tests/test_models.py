# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The routes of Models: the catalogue with each model's mirror state, the
mirror jobs, the count Control's top bar shows, the read Control's LLM
Gateway makes inside the cluster, and the publishing of a fine-tune."""

import httpx
import pytest

import app.api.models as models_api
from app.core.security import User, get_current_user
from app.main import app
from app.models.model_mirrors import ModelMirrorJob
from app.services import metadata
from app.services.metadata import CatalogUnavailableError

CATALOG = [
    {"id": "Qwen/Qwen3.5-4B", "name": "Qwen3.5 4B", "quantization": "BF16", "description": "d",
     "server_type": ["vllm"], "params_b": 4.0},
    {"id": "ollama/gemma3:4b", "name": "Gemma 3 4B", "quantization": "Q4_K_M", "description": "d",
     "server_type": ["ollama"], "size": "3.3GB"},
]


@pytest.fixture
def signed_in():
    app.dependency_overrides[get_current_user] = lambda: User(sub="u-1", preferred_username="thinkube")
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def catalogue(monkeypatch):
    """The catalogue as get_available_models reads it, and the mirrored models from the jobs."""
    class Service:
        def get_available_models(self):
            return [dict(m) for m in CATALOG]

        def check_all_models_exist(self):
            return {"Qwen/Qwen3.5-4B": True, "ollama/gemma3:4b": False}

    monkeypatch.setattr(models_api, "ModelDownloaderService", Service)


def test_the_catalogue_says_which_models_are_mirrored_and_their_size(client, signed_in, catalogue):
    r = client.get("/api/v1/models/catalog")
    assert r.status_code == 200, r.text
    listed = {m["id"]: m for m in r.json()["models"]}
    assert listed["Qwen/Qwen3.5-4B"]["is_downloaded"] is True
    assert listed["Qwen/Qwen3.5-4B"]["size"] == "~8GB"
    assert listed["ollama/gemma3:4b"]["is_downloaded"] is False
    assert listed["ollama/gemma3:4b"]["size"] == "3.3GB"


def test_controls_read_needs_no_token_and_is_outside_the_api(client, catalogue):
    r = client.get("/internal/models")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["models"]] == ["Qwen/Qwen3.5-4B", "ollama/gemma3:4b"]
    assert client.get("/api/v1/internal/models").status_code == 404


def test_a_fresh_read_forgets_the_catalogue_in_memory(client, catalogue, monkeypatch):
    forgotten = []
    monkeypatch.setattr("app.api.internal.invalidate_catalogs", lambda: forgotten.append(True))
    client.get("/internal/models?fresh=true")
    assert forgotten == [True]


def test_a_catalogue_that_cannot_be_read_answers_502_with_the_reason(client, signed_in, monkeypatch):
    def unavailable():
        raise CatalogUnavailableError("Cannot fetch catalog https://raw.githubusercontent.com/x: HTTP 500")

    class Service:
        get_available_models = staticmethod(unavailable)

    monkeypatch.setattr(models_api, "ModelDownloaderService", Service)
    r = client.get("/api/v1/models/catalog")
    assert r.status_code == 502
    assert "raw.githubusercontent.com" in r.json()["detail"]


def test_a_model_outside_the_catalogue_is_refused_and_leaves_no_job(client, signed_in, test_db, monkeypatch):
    def not_found(model_id):
        raise ValueError(f"Model '{model_id}' not found in catalog")

    monkeypatch.setattr(models_api, "catalog_entry", not_found)
    r = client.post("/api/v1/models/mirrors", json={"model_id": "nobody/nothing"})
    assert r.status_code == 400
    assert test_db.query(ModelMirrorJob).count() == 0


def test_a_model_already_mirroring_is_refused(client, signed_in, test_db, monkeypatch):
    monkeypatch.setattr(models_api, "catalog_entry", lambda model_id: {"id": model_id})
    test_db.add(ModelMirrorJob(model_id="Qwen/Qwen3.5-4B", status="running", workflow_name="model-dl-a"))
    test_db.commit()
    r = client.post("/api/v1/models/mirrors", json={"model_id": "Qwen/Qwen3.5-4B"})
    assert r.status_code == 409


def test_a_mirror_is_submitted_and_recorded_as_running(client, signed_in, test_db, monkeypatch):
    monkeypatch.setattr(models_api, "catalog_entry", lambda model_id: {"id": model_id})

    class Service:
        def submit_download(self, model_id):
            return "model-dl-xyz"

    monkeypatch.setattr(models_api, "ModelDownloaderService", Service)
    r = client.post("/api/v1/models/mirrors", json={"model_id": "Qwen/Qwen3.5-4B"})
    assert r.status_code == 200, r.text
    assert r.json()["workflow_id"] == "model-dl-xyz"
    job = test_db.query(ModelMirrorJob).filter_by(model_id="Qwen/Qwen3.5-4B").one()
    assert (job.status, job.workflow_name) == ("running", "model-dl-xyz")


def test_the_top_bar_count_is_the_mirrors_still_running(client, signed_in, test_db):
    test_db.add_all([
        ModelMirrorJob(model_id="a/one", status="running", workflow_name="model-dl-1"),
        ModelMirrorJob(model_id="a/two", status="pending"),
        ModelMirrorJob(model_id="a/three", status="succeeded"),
    ])
    test_db.commit()
    assert client.get("/api/v1/models/mirrors/active").json() == {"count": 2}


def test_a_job_links_its_workflow_in_argo(client, signed_in, test_db):
    test_db.add(ModelMirrorJob(model_id="a/one", status="failed", workflow_name="model-dl-1"))
    test_db.commit()
    listed = client.get("/api/v1/models/mirrors").json()["jobs"][0]
    assert listed["workflow_url"] == "https://argo.test.com/workflows/argo/model-dl-1"


def test_an_unknown_workflow_is_404(client, signed_in):
    assert client.get("/api/v1/models/mirrors/model-dl-none").status_code == 404


ENTRY = {
    "id": "forge/greeter", "name": "Greeter", "params_b": 2.25, "active_params_b": None, "quantization": "BF16",
    "context_length": 8192, "description": "d", "server_type": ["vllm"], "task": "text-generation",
    "reasoning_format": None, "tool_use": False, "stop_tokens": [], "license": "apache-2.0", "gated": False,
    "serving_name": "forge/greeter", "is_finetuned": True,
}


@pytest.fixture
def control(monkeypatch):
    """Control's LLM Gateway: the refresh and the model's status, with the calls it was asked."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/llm/refresh/"):
            return httpx.Response(200, json={"models_refreshed": 1})
        return httpx.Response(200, json={"model": {"id": ENTRY["id"], "state": "deployable"}, "backends": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(models_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_a_fine_tune_is_written_recorded_and_read_by_the_gateway(client, test_db, monkeypatch, control):
    app.dependency_overrides[get_current_user] = lambda: User(sub="u-1", preferred_username="thinkube")
    monkeypatch.setattr(models_api.finetuned_models, "write_entry", lambda entry: "alice/alice-metadata")
    try:
        r = client.post("/api/v1/models/finetuned", json={"catalog_entry": ENTRY},
                        headers={"Authorization": "Bearer tk_user"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 200, r.text
    assert r.json() == {"model_id": "forge/greeter", "repository": "alice/alice-metadata", "state": "deployable"}
    assert test_db.query(ModelMirrorJob).filter_by(model_id="forge/greeter").one().status == "succeeded"
    assert [str(c.url) for c in control] == [
        f"{models_api.settings.CONTROL_API_URL}/llm/refresh/",
        f"{models_api.settings.CONTROL_API_URL}/llm/models/forge/greeter/status",
    ]
    assert all(c.headers["authorization"] == "Bearer tk_user" for c in control)


def test_an_incomplete_fine_tune_entry_is_refused(client, signed_in):
    r = client.post("/api/v1/models/finetuned", json={"catalog_entry": {"id": "forge/x"}},
                    headers={"Authorization": "Bearer tk_user"})
    assert r.status_code == 422
    assert "missing" in r.json()["detail"]


def test_the_catalogue_is_read_at_the_branch_the_cluster_follows(monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "release-0.1")
    assert metadata.platform_metadata_url("models.json").endswith("/thinkube-metadata/release-0.1/models.json")
