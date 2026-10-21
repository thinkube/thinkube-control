# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The CI/CD pipelines list stays cheap under bursts of polls.

It lists only workflow metadata, reads in full only the newest `limit`
workflows, and requests that arrive within LIST_REUSE_SECONDS share one read.
"""

import json
import threading

import app.api.cicd as cicd


class _Raw:
    def __init__(self, body):
        self.data = json.dumps(body).encode()


class _FakeApiClient:
    def __init__(self, metas):
        self.metas = metas
        self.calls = []

    def call_api(self, path, method, **kwargs):
        self.calls.append((path, method, kwargs))
        return _Raw({"kind": "PartialObjectMetadataList", "items": [{"metadata": m} for m in self.metas]})


class _FakeCustomApi:
    def __init__(self, metas):
        self.api_client = _FakeApiClient(metas)
        self.read = []
        self.lock = threading.Lock()

    def get_namespaced_custom_object(self, group, version, namespace, plural, name, **kwargs):
        with self.lock:
            self.read.append(name)
        return {
            "metadata": {"name": name, "labels": {"thinkube.io/app-name": "app"}},
            "status": {"phase": "Succeeded"},
            "spec": {},
        }


def _metas(n):
    return [
        {"name": f"wf-{i:03d}", "creationTimestamp": f"2026-10-20T10:{i // 60:02d}:{i % 60:02d}Z"}
        for i in range(n)
    ]


def _install(monkeypatch, api):
    monkeypatch.setattr(cicd, "_get_k8s_clients", lambda: (api, None, None))
    monkeypatch.setattr(cicd, "_list_kept", {})


def test_lists_metadata_only_and_reads_the_newest(monkeypatch):
    api = _FakeCustomApi(_metas(50))
    _install(monkeypatch, api)

    result = cicd.list_pipelines(app_name=None, status=None, limit=5, current_user={})

    (path, method, kwargs), = api.api_client.calls
    assert path.endswith("/namespaces/argo/workflows") and method == "GET"
    assert "as=PartialObjectMetadataList" in kwargs["header_params"]["Accept"]
    assert kwargs["_preload_content"] is False
    assert ("labelSelector", "thinkube.io/app-name") in kwargs["query_params"]
    assert api.read == ["wf-049", "wf-048", "wf-047", "wf-046", "wf-045"]
    assert [p["id"] for p in result["pipelines"]] == api.read
    assert all(p["stages"] == [] for p in result["pipelines"])


def test_parallel_requests_share_one_read(monkeypatch):
    api = _FakeCustomApi(_metas(30))
    _install(monkeypatch, api)

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(
            cicd.list_pipelines(app_name=None, status=None, limit=20, current_user={})))
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(api.api_client.calls) == 1
    assert len(api.read) == 20
    assert len(results) == 10 and all(r is results[0] for r in results)


def test_other_filters_are_read_on_their_own(monkeypatch):
    api = _FakeCustomApi(_metas(3))
    _install(monkeypatch, api)

    cicd.list_pipelines(app_name=None, status=None, limit=20, current_user={})
    cicd.list_pipelines(app_name="app", status=None, limit=20, current_user={})

    assert len(api.api_client.calls) == 2
    assert ("labelSelector", "thinkube.io/app-name=app") in api.api_client.calls[1][2]["query_params"]
