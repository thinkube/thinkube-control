#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""User components: optional_components.json in the user's metadata repository.

They join the platform's components when each entry installs from a
template; anything else makes the catalog unavailable, with the entry named.
"""

import io
import json
import urllib.error

import pytest

from app.services import optional_components as oc
from app.services.metadata_fetcher import CatalogUnavailableError

PLATFORM = {"components": {"vllm": {
    "display_name": "vLLM", "description": "d", "category": "ai", "requirements": [],
    "namespace": "vllm", "template": {"url": "https://github.com/thinkube/tkt-vllm-gradio", "fixed_name": "vllm"},
}}}
APERTIUM = {
    "display_name": "Apertium", "description": "Rule-based machine translation (GPL)", "category": "ai",
    "requirements": [], "namespace": "apertium", "architectures": ["amd64"],
    "template": {"url": "https://github.com/kubexlat/tkt-apertium", "fixed_name": "apertium"},
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "release-0.1")
    monkeypatch.setenv("GITHUB_USERNAME", "someone")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0
    yield
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0


def serve(monkeypatch, user):
    """The platform catalog, and the user's (None: the user's file does not exist)."""
    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "someone-metadata" in url:
            if user is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return Resp(user)
        return Resp(PLATFORM)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)


def test_a_user_without_the_file_sees_the_platform_components(monkeypatch):
    serve(monkeypatch, None)
    assert set(oc.get_components_catalog()) == {"vllm"}


def test_a_template_component_of_the_user_joins_the_catalog(monkeypatch):
    serve(monkeypatch, {"components": {"apertium": APERTIUM}})
    catalog = oc.get_components_catalog()
    assert set(catalog) == {"vllm", "apertium"}
    assert catalog["apertium"]["source"] == "user"
    assert catalog["apertium"]["architectures"] == ["amd64"]


@pytest.mark.parametrize("change, message", [
    ({"template": None}, "no template.url"),
    ({"playbooks": {"install": "00_install.yaml"}}, "declares playbooks"),
    ({"namespace": "translation"}, "deployed into 'apertium'"),
    ({"display_name": None}, "lacks display_name"),
])
def test_an_entry_that_cannot_install_from_a_template_is_refused(monkeypatch, change, message):
    entry = {k: v for k, v in {**APERTIUM, **change}.items() if v is not None}
    serve(monkeypatch, {"components": {"apertium": entry}})
    with pytest.raises(CatalogUnavailableError, match=message):
        oc.get_components_catalog()


def test_a_user_component_cannot_take_a_platform_name(monkeypatch):
    serve(monkeypatch, {"components": {"vllm": {**APERTIUM, "namespace": "vllm",
                                                "template": {"url": "https://x", "fixed_name": "vllm"}}}})
    with pytest.raises(CatalogUnavailableError, match="name of a platform component"):
        oc.get_components_catalog()
