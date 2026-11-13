# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Control reads the model catalogue from the extension Models; a read that
fails is an error naming the extension's address and answer, never an empty
catalogue."""

import httpx
import pytest

from app.services import models_catalog
from app.services.metadata_fetcher import CatalogUnavailableError


def answering(monkeypatch, answer):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append((url, params))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(models_catalog.httpx, "get", get)
    return seen


def test_the_catalogue_is_the_extensions_answer(monkeypatch):
    models = [{"id": "Qwen/Qwen3.5-4B", "is_downloaded": True, "size": "~8GB"}]
    seen = answering(monkeypatch, httpx.Response(200, json={"models": models}))
    assert models_catalog.catalogue() == models
    assert seen == [("http://control-models-backend:8000/internal/models", None)]


def test_a_fresh_read_asks_the_extension_to_read_the_repositories_again(monkeypatch):
    seen = answering(monkeypatch, httpx.Response(200, json={"models": []}))
    models_catalog.catalogue(fresh=True)
    assert seen[0][1] == {"fresh": "true"}


def test_an_extension_that_does_not_answer_is_named(monkeypatch):
    answering(monkeypatch, httpx.ConnectError("Name or service not known"))
    with pytest.raises(CatalogUnavailableError, match="control-models-backend.*Is control-models deployed"):
        models_catalog.catalogue()


def test_an_extension_that_fails_gives_its_answer(monkeypatch):
    answering(monkeypatch, httpx.Response(502, text="Cannot fetch catalog https://raw.githubusercontent.com/x"))
    with pytest.raises(CatalogUnavailableError, match="HTTP 502.*raw.githubusercontent.com"):
        models_catalog.catalogue()
