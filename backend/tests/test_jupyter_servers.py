"""Notebook servers per node: defaults fill the gaps, the node's choices and capacity bound the ask, and a notebook operation finds its server."""

import asyncio

import pytest
from fastapi import HTTPException

import app.api.jupyter_notebooks as jn
import app.api.jupyterhub_config as jc
import app.api.jupyter_servers as js
from app.models.jupyterhub_config import JupyterHubConfig, JupyterHubNodeDefaults


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class FakeDB:
    def __init__(self, fallback=None, node_rows=()):
        self.fallback = fallback
        self.node_rows = list(node_rows)

    def query(self, model):
        if model is JupyterHubNodeDefaults:
            return FakeQuery(self.node_rows)
        return FakeQuery([self.fallback] if self.fallback else [])


NODES = [
    {
        "name": "tkspark",
        "capacity": {"cpu": 20, "memory": "119Gi", "gpu": 4, "effective_gpu": 1},
        "allocated": {"cpu": 2, "memory": "8Gi", "gpu": 1},
        "available": {"cpu": 18, "memory": "111Gi", "gpu": 3},
    },
    {
        "name": "tkamd1",
        "capacity": {"cpu": 16, "memory": "62Gi", "gpu": 0, "effective_gpu": 0},
        "allocated": {"cpu": 4, "memory": "16Gi", "gpu": 0},
        "available": {"cpu": 12, "memory": "46Gi", "gpu": 0},
    },
]


@pytest.fixture(autouse=True)
def nodes(monkeypatch):
    async def fake():
        return NODES

    monkeypatch.setattr(js, "get_cluster_resources", fake)


def test_node_defaults_fill_what_was_not_said():
    db = FakeDB(node_rows=[JupyterHubNodeDefaults(node="tkspark", cpu_cores=8, memory_gb=32, gpus=1)])
    placement = asyncio.run(js.resolve_placement(db, "tkspark", None, None, None))
    assert placement.user_options() == {"profile": "thinkube-notebooks", "node": "tkspark", "cpu": "8", "memory": "32G", "enable_gpu": "1"}


def test_a_node_without_its_own_defaults_starts_from_the_fallback():
    db = FakeDB(fallback=JupyterHubConfig(default_cpu_cores=4, default_memory_gb=8, default_gpu_count=0))
    placement = asyncio.run(js.resolve_placement(db, "tkamd1", None, None, None))
    assert (placement.cpu_cores, placement.memory_gb, placement.gpus) == (4, 8, 0)


def test_saved_defaults_are_moved_onto_the_node_choices():
    db = FakeDB(node_rows=[JupyterHubNodeDefaults(node="tkamd1", cpu_cores=64, memory_gb=100, gpus=2)])
    (amd,) = [d for d in jc.defaults_for_nodes(db, NODES) if d.node == "tkamd1"]
    assert (amd.cpu_cores, amd.memory_gb, amd.gpus) == (16, 48, 0)
    assert amd.choices["cpu_cores"] == [1, 2, 4, 6, 8, 12, 16]
    assert amd.choices["memory_gb"] == [2, 4, 8, 16, 32, 48]
    assert amd.choices["gpus"] == [0]


def test_values_outside_the_choices_are_refused():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), "tkamd1", 5, 8, 0))
    assert "cpu_cores must be one of" in e.value.detail
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), "tkamd1", 4, 64, 0))
    assert "memory_gb must be one of" in e.value.detail


def test_gpu_on_spark_is_capped_to_one_slot():
    placement = asyncio.run(js.resolve_placement(FakeDB(), "tkspark", 8, 32, 1))
    assert placement.gpus == 1
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), "tkspark", 8, 32, 2))
    assert "gpus must be between 0 and 1" in e.value.detail


def test_no_gpu_node_refuses_a_gpu():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), "tkamd1", 4, 8, 1))
    assert "has no GPU" in e.value.detail


def test_node_is_required_and_must_exist():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), "nowhere", 4, 8, 0))
    assert "not in the cluster" in e.value.detail
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(FakeDB(), None, 4, 8, 0))
    assert "node is required" in e.value.detail


def test_placement_read_back_from_hub_options():
    assert js._placement_of({"user_options": {"node": "tkspark", "cpu": "8", "memory": "32G", "enable_gpu": "1"}}) == {
        "node": "tkspark", "cpu_cores": 8, "memory_gb": 32, "gpus": 1,
    }
    assert js._placement_of(None) == {"node": None, "cpu_cores": None, "memory_gb": None, "gpus": None}


def test_server_state_from_the_hub_model():
    assert js._state(None) == "stopped"
    assert js._state({"ready": True}) == "running"
    assert js._state({"ready": False, "pending": "spawn"}) == "starting"
    assert js._state({"ready": False, "pending": "stop"}) == "stopping"


def running(monkeypatch, names):
    monkeypatch.setattr(jn, "running_server_pods", lambda: {name: object() for name in names})


def test_the_only_interactive_server_is_used_without_a_node(monkeypatch):
    running(monkeypatch, ["tkspark", "job-1a2b3c4d"])
    assert jn.resolve_server(None) == "tkspark"


def test_several_servers_need_a_node(monkeypatch):
    running(monkeypatch, ["tkspark", "tkamd1"])
    with pytest.raises(HTTPException) as e:
        jn.resolve_server(None)
    assert e.value.status_code == 409
    assert "tkamd1, tkspark" in e.value.detail
    assert jn.resolve_server("tkamd1") == "tkamd1"


def test_a_node_without_a_server_and_no_server_at_all(monkeypatch):
    running(monkeypatch, ["tkspark"])
    with pytest.raises(HTTPException) as e:
        jn.resolve_server("tkamd1")
    assert e.value.status_code == 503 and "tkamd1" in e.value.detail
    running(monkeypatch, [])
    with pytest.raises(HTTPException) as e:
        jn.resolve_server(None)
    assert e.value.status_code == 503


class FakePod:
    def __init__(self, labels):
        self.metadata = type("Meta", (), {"labels": labels, "deletion_timestamp": None})()
        self.status = type("Status", (), {"phase": "Running"})()


def test_the_hub_default_server_is_not_a_notebook_server(monkeypatch):
    pods = [FakePod({"hub.jupyter.org/servername": "tkspark"}), FakePod({}), FakePod({"hub.jupyter.org/servername": ""})]

    class FakeV1:
        def list_namespaced_pod(self, namespace, label_selector):
            return type("List", (), {"items": pods})()

    monkeypatch.setattr(jn, "_load_kube", lambda: FakeV1())
    assert list(jn.running_server_pods()) == ["tkspark"]
    with pytest.raises(js.hub.HubError):
        js.hub.server_path("")


class FakeHub:
    """The Hub as the start endpoint sees it: one server's model, and what was asked of it."""

    HubError = js.hub.HubError

    def __init__(self, model=None, ready_error=None):
        self.model = model
        self.ready_error = ready_error
        self.started = []

    async def server(self, name=""):
        return self.model

    async def start_server(self, name, user_options):
        self.started.append((name, user_options))
        self.model = {"ready": False, "pending": "spawn", "user_options": user_options}
        return 202

    async def wait_ready(self, name, timeout=240.0, interval=3.0):
        await asyncio.sleep(0)
        if self.ready_error:
            raise js.hub.HubError(self.ready_error)
        return {"ready": True}


def start(monkeypatch, fake_hub, node="tkamd1"):
    monkeypatch.setattr(js, "hub", fake_hub)

    async def status(db, n):
        return n

    monkeypatch.setattr(js, "_node_status", status)

    async def run():
        answer = await js.start_notebook_server(node, None, current_user={}, db=FakeDB())
        watcher = js._start_watchers.get(node)
        if watcher:
            await watcher
        return answer

    return asyncio.run(run())


def test_a_start_answers_once_the_hub_has_it(monkeypatch):
    fake_hub = FakeHub()
    assert start(monkeypatch, fake_hub) == "tkamd1"
    assert fake_hub.started == [("tkamd1", {"profile": "thinkube-notebooks", "node": "tkamd1", "cpu": "4", "memory": "8G", "enable_gpu": "0"})]
    assert "tkamd1" not in js._start_failures


def test_a_start_the_hub_gives_up_on_keeps_its_reason_until_the_next_start(monkeypatch):
    start(monkeypatch, FakeHub(ready_error="the server 'tkamd1' stopped before it was ready; see the Hub's log"))
    assert js._start_failures["tkamd1"].startswith("the server 'tkamd1' stopped before it was ready")
    start(monkeypatch, FakeHub())
    assert "tkamd1" not in js._start_failures


def test_a_running_server_is_not_started_again(monkeypatch):
    with pytest.raises(HTTPException) as e:
        start(monkeypatch, FakeHub(model={"ready": True, "user_options": {"enable_gpu": "0"}}))
    assert e.value.status_code == 409
