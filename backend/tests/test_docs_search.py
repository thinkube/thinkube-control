# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Docs search finds the docs app among thinkube-control's own deployments.

The docs app is the one deployed from the thinkube.org template. With no such
deployment the tools answer docs_not_deployed. With one, its index is read from
http://<app>.<app>.svc.cluster.local:8080/search-index.js, and a failure to
fetch or read it is HTTP 502 naming that URL.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import app.api.docs_search as ds

INDEX = "antora.registerSearchIndex(" + json.dumps({
    "store": {"documents": {
        "1": {"title": "Deploy a template", "url": "/deploy.html", "name": "deploy", "text": "How to deploy a template."},
    }}
}) + ")"


class Query:
    def __init__(self, record):
        self.record = record

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return self.record


class Db:
    def __init__(self, record):
        self.record = record

    def query(self, model):
        return Query(self.record)


def deployed(name):
    return Db(SimpleNamespace(name=name))


def serve(monkeypatch, handler):
    real = httpx.AsyncClient
    seen = []

    def record(request):
        seen.append(str(request.url))
        return handler(request)

    monkeypatch.setattr(
        ds.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(record), **kw),
    )
    return seen


def search(db, query="deploy"):
    return asyncio.run(ds.search_thinkube_docs(query=query, db=db))


def test_no_docs_deployment_answers_not_deployed(monkeypatch):
    seen = serve(monkeypatch, lambda r: httpx.Response(200, text=INDEX))

    assert search(Db(None))["status"] == "docs_not_deployed"
    assert seen == []


def test_the_index_is_read_from_the_deployed_app(monkeypatch):
    seen = serve(monkeypatch, lambda r: httpx.Response(200, text=INDEX))

    result = search(deployed("docs"))

    assert seen == ["http://docs.docs.svc.cluster.local:8080/search-index.js"]
    assert result["results"][0]["name"] == "deploy"


def test_an_app_under_another_name_is_found_by_that_name(monkeypatch):
    seen = serve(monkeypatch, lambda r: httpx.Response(200, text=INDEX))

    search(deployed("handbook"))

    assert seen == ["http://handbook.handbook.svc.cluster.local:8080/search-index.js"]


def test_an_unreachable_index_is_an_error_naming_the_url(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    serve(monkeypatch, refuse)

    with pytest.raises(HTTPException) as err:
        search(deployed("docs"))
    assert err.value.status_code == 502
    assert "http://docs.docs.svc.cluster.local:8080/search-index.js" in err.value.detail
    assert "connection refused" in err.value.detail


def test_an_http_error_is_an_error_naming_the_status(monkeypatch):
    serve(monkeypatch, lambda r: httpx.Response(404, text="nope"))

    with pytest.raises(HTTPException) as err:
        search(deployed("docs"))
    assert err.value.status_code == 502
    assert "HTTP 404" in err.value.detail


def test_an_unreadable_index_is_an_error(monkeypatch):
    serve(monkeypatch, lambda r: httpx.Response(200, text="<html>no index</html>"))

    with pytest.raises(HTTPException) as err:
        asyncio.run(ds.get_thinkube_doc(page="deploy", db=deployed("docs")))
    assert err.value.status_code == 502
    assert "Cannot parse the docs index" in err.value.detail
