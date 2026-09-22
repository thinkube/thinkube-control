# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A metadata catalog that cannot be fetched is an error, never an empty list.

fetch_merged_catalog keeps a fresh copy in memory for a TTL. When the fetch
fails and no fresh copy is held, it raises CatalogUnavailableError naming the
URL and the failure, and the API answers HTTP 502 with that message.
"""

import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.services import metadata_fetcher as mf
from app.services import model_downloader
from app.services import optional_components as oc
from app.services.metadata_fetcher import CatalogUnavailableError

PLATFORM_URL = mf._github_raw_url("thinkube", "thinkube-metadata", "repositories.json")
USER_URL = mf._github_raw_url("someone", "someone-metadata", "repositories.json")
REPOS = {"repositories": [{"name": "tkt-webapp", "type": "application_template"}]}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    mf._memory_cache.clear()
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0
    monkeypatch.delenv("GITHUB_USERNAME", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    yield
    mf._memory_cache.clear()
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0


class Resp:
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(monkeypatch, answers):
    """urlopen answers from a {url: payload | Exception} map."""

    def urlopen(req, timeout=None):
        answer = answers[req.full_url]
        if isinstance(answer, Exception):
            raise answer
        return Resp(answer)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "Not Found" if code == 404 else "Server Error", {}, io.BytesIO())


def fetch_repositories():
    return mf.fetch_merged_catalog(
        catalog_name="repositories",
        file_name="repositories.json",
        extract_key="repositories",
        merge_strategy="list",
        dedup_key="name",
    )


def test_a_failed_fetch_raises_an_error_naming_the_url(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: OSError("handshake timed out")})

    with pytest.raises(CatalogUnavailableError) as err:
        fetch_repositories()
    assert PLATFORM_URL in str(err.value)
    assert "handshake timed out" in str(err.value)
    assert "repositories" not in mf._memory_cache


def test_an_http_error_names_the_status(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: http_error(PLATFORM_URL, 503)})

    with pytest.raises(CatalogUnavailableError, match="HTTP 503"):
        fetch_repositories()


def test_a_fresh_copy_is_served_from_memory(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: REPOS})
    assert fetch_repositories()[0]["name"] == "tkt-webapp"

    serve(monkeypatch, {PLATFORM_URL: OSError("down")})

    assert fetch_repositories()[0]["name"] == "tkt-webapp"


def test_an_expired_copy_is_not_served_when_the_fetch_fails(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: REPOS})
    fetch_repositories()
    mf._memory_cache["repositories"]["time"] = 0

    serve(monkeypatch, {PLATFORM_URL: OSError("down")})

    with pytest.raises(CatalogUnavailableError):
        fetch_repositories()


def test_a_catalog_without_its_key_raises(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: {"version": "1"}})

    with pytest.raises(CatalogUnavailableError, match="no 'repositories' key"):
        fetch_repositories()


def test_invalid_json_raises(monkeypatch):
    serve(monkeypatch, {PLATFORM_URL: b"<html>"})

    with pytest.raises(CatalogUnavailableError, match="not valid JSON"):
        fetch_repositories()


def test_a_user_catalog_that_does_not_exist_adds_no_entries(monkeypatch):
    monkeypatch.setenv("GITHUB_USERNAME", "someone")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    serve(monkeypatch, {PLATFORM_URL: REPOS, USER_URL: http_error(USER_URL, 404)})

    assert [r["name"] for r in fetch_repositories()] == ["tkt-webapp"]


def test_a_user_catalog_that_fails_raises(monkeypatch):
    monkeypatch.setenv("GITHUB_USERNAME", "someone")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    serve(monkeypatch, {PLATFORM_URL: REPOS, USER_URL: http_error(USER_URL, 500)})

    with pytest.raises(CatalogUnavailableError) as err:
        fetch_repositories()
    assert USER_URL in str(err.value)


def test_user_entries_are_merged(monkeypatch):
    monkeypatch.setenv("GITHUB_USERNAME", "someone")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    user = {"repositories": [{"name": "mine", "type": "application_template"}]}
    serve(monkeypatch, {PLATFORM_URL: REPOS, USER_URL: user})

    assert [r["name"] for r in fetch_repositories()] == ["tkt-webapp", "mine"]


@pytest.fixture
def api(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_current_user_dual_auth] = lambda: {"preferred_username": "t"}
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


def all_fetches_fail(monkeypatch):
    def urlopen(req, timeout=None):
        raise OSError("network is unreachable")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)


def test_template_list_answers_502_naming_the_url(monkeypatch, api):
    all_fetches_fail(monkeypatch)

    r = api.get("/api/v1/templates/list")

    assert r.status_code == 502
    assert PLATFORM_URL in r.json()["detail"]
    assert "network is unreachable" in r.json()["detail"]


def test_optional_components_list_answers_502_naming_the_url(monkeypatch, api):
    all_fetches_fail(monkeypatch)

    r = api.get("/api/v1/optional-components/list")

    assert r.status_code == 502
    assert oc._COMPONENTS_CATALOG_URL in r.json()["detail"]


def test_model_catalog_answers_502_naming_the_url(monkeypatch, api):
    import app.api.model_mirrors as model_mirrors

    class Downloader:
        def get_available_models(self):
            return model_downloader.get_model_catalog()

    monkeypatch.setattr(model_mirrors, "ModelDownloaderService", Downloader)
    all_fetches_fail(monkeypatch)

    r = api.get("/api/v1/models/catalog")

    assert r.status_code == 502
    assert mf._github_raw_url("thinkube", "thinkube-metadata", "models.json") in r.json()["detail"]
