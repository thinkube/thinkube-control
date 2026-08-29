#!/usr/bin/env python3
"""Tests that a failed catalog fetch does not empty the menu for a whole TTL.

The catalog is fetched from thinkube-metadata and cached. Caching the empty
result of a failed fetch, with a fresh timestamp, held every component out of
the menu until the TTL expired - and the retry at expiry re-poisoned it.
"""

import pytest

from app.services import optional_components as oc

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


def test_a_failed_fetch_returns_empty_without_caching_it(monkeypatch):
    fetch_fails(monkeypatch)

    assert oc.get_components_catalog() == {}
    assert oc._COMPONENTS_CATALOG_CACHE is None


def test_the_next_call_retries_after_a_failure(monkeypatch):
    fetch_fails(monkeypatch)
    assert oc.get_components_catalog() == {}

    fetch_returns(monkeypatch, CATALOG)

    assert "qdrant" in oc.get_components_catalog()


def test_a_successful_fetch_is_cached(monkeypatch):
    fetch_returns(monkeypatch, CATALOG)
    assert "qdrant" in oc.get_components_catalog()

    fetch_fails(monkeypatch)

    assert "qdrant" in oc.get_components_catalog()


def test_a_later_failure_falls_back_to_the_cached_catalog(monkeypatch):
    fetch_returns(monkeypatch, CATALOG)
    oc.get_components_catalog()

    oc._COMPONENTS_CATALOG_CACHE_TIME = 0  # force expiry
    fetch_fails(monkeypatch)

    assert "qdrant" in oc.get_components_catalog()
