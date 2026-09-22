#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The components catalog is fetched from thinkube-metadata and kept in
memory for a TTL. A failed fetch with no fresh copy raises an error naming
the URL; it is never cached and never answered with an empty or stale catalog.
"""

import pytest

from app.services import optional_components as oc
from app.services.metadata_fetcher import CatalogUnavailableError

CATALOG = {"components": {"qdrant": {"display_name": "Qdrant"}}}


@pytest.fixture(autouse=True)
def clean_cache():
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0
    yield
    oc._COMPONENTS_CATALOG_CACHE = None
    oc._COMPONENTS_CATALOG_CACHE_TIME = 0


def fetch_fails(monkeypatch):
    def boom(*a, **k):
        raise OSError("handshake timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)


def fetch_returns(monkeypatch, payload):
    import io
    import json

    class Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())


def test_a_failed_fetch_raises_an_error_naming_the_url(monkeypatch):
    fetch_fails(monkeypatch)

    with pytest.raises(CatalogUnavailableError) as err:
        oc.get_components_catalog()
    assert oc._COMPONENTS_CATALOG_URL in str(err.value)
    assert "handshake timed out" in str(err.value)
    assert oc._COMPONENTS_CATALOG_CACHE is None


def test_the_next_call_retries_after_a_failure(monkeypatch):
    fetch_fails(monkeypatch)
    with pytest.raises(CatalogUnavailableError):
        oc.get_components_catalog()

    fetch_returns(monkeypatch, CATALOG)

    assert "qdrant" in oc.get_components_catalog()


def test_a_successful_fetch_is_cached(monkeypatch):
    fetch_returns(monkeypatch, CATALOG)
    assert "qdrant" in oc.get_components_catalog()

    fetch_fails(monkeypatch)

    assert "qdrant" in oc.get_components_catalog()


def test_a_failure_after_the_ttl_raises_instead_of_serving_the_stale_catalog(monkeypatch):
    fetch_returns(monkeypatch, CATALOG)
    oc.get_components_catalog()

    oc._COMPONENTS_CATALOG_CACHE_TIME = 0  # force expiry
    fetch_fails(monkeypatch)

    with pytest.raises(CatalogUnavailableError):
        oc.get_components_catalog()


def test_a_catalog_without_components_raises(monkeypatch):
    fetch_returns(monkeypatch, {"version": "1"})

    with pytest.raises(CatalogUnavailableError, match="components"):
        oc.get_components_catalog()
