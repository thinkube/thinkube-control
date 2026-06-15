"""cluster-resources refresh must not stall the event loop (SP-tgnbej SL-3).

Deterministic structural checks (not flaky timing): the refresh is throttled and
the pod fetch is offloaded + bounded, so it cannot block the resolve path.
"""

import json

import app.api.cluster_resources as cr


def test_refresh_cadence_is_throttled():
    # An infrequent refresh means the periodic (CPU/GIL-bound) recompute can't
    # repeatedly stall the loop.
    assert cr._REFRESH_INTERVAL_SECONDS >= 60
    assert cr._CACHE_TTL_SECONDS >= cr._REFRESH_INTERVAL_SECONDS


class _FakeNodeList:
    items: list = []


class _FakeRaw:
    data = json.dumps({"items": []})


class _FakeV1:
    def __init__(self):
        self.pod_kwargs = None

    def list_node(self):
        return _FakeNodeList()

    def list_pod_for_all_namespaces(self, **kwargs):
        self.pod_kwargs = kwargs
        return _FakeRaw()


def test_compute_uses_bounded_lightweight_pod_fetch(monkeypatch):
    fake = _FakeV1()
    monkeypatch.setattr(cr.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(cr.client, "CoreV1Api", lambda: fake)

    result = cr._compute_cluster_resources()
    assert result == []  # no nodes -> empty

    # The pod list must avoid building a typed V1Pod object graph (the dominant
    # GIL cost) and drop terminated pods server-side, so a refresh stays cheap.
    assert fake.pod_kwargs is not None
    assert fake.pod_kwargs.get("_preload_content") is False
    fs = fake.pod_kwargs.get("field_selector", "")
    assert "status.phase!=Succeeded" in fs
    assert "status.phase!=Failed" in fs


def test_refresh_is_offloaded_off_the_event_loop():
    # _refresh_once must run the blocking compute via asyncio.to_thread, not on
    # the event loop. Assert the source does exactly that.
    import inspect

    src = inspect.getsource(cr._refresh_once)
    assert "to_thread" in src and "_compute_cluster_resources" in src
