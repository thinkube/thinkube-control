"""Placement of a notebook server: defaults fill the gaps, the node's capacity bounds the ask."""

import asyncio

import pytest
from fastapi import HTTPException

import app.api.jupyter_servers as js


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row

    def query(self, model):
        return FakeQuery(self.row)


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


def defaults(node=None, cpu=4, mem=8, gpu=0):
    return FakeDB(js.JupyterHubConfig(default_node=node, default_cpu_cores=cpu, default_memory_gb=mem, default_gpu_count=gpu))


def test_defaults_fill_what_was_not_said():
    placement = asyncio.run(js.resolve_placement(defaults(node="tkamd1"), None, None, None, None))
    assert placement.user_options() == {"profile": "thinkube-ai-lab", "node": "tkamd1", "cpu": "4", "memory": "8G", "enable_gpu": "0"}


def test_gpu_on_spark_is_capped_to_one_slot():
    placement = asyncio.run(js.resolve_placement(defaults(), "tkspark", 8, 32, 1))
    assert placement.gpus == 1
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(defaults(), "tkspark", 8, 32, 2))
    assert "gpus must be between 0 and 1" in e.value.detail


def test_no_gpu_node_refuses_a_gpu():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(defaults(), "tkamd1", 4, 8, 1))
    assert "has no GPU" in e.value.detail


def test_unknown_node_and_missing_default():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(defaults(), "nowhere", 4, 8, 0))
    assert "not in the cluster" in e.value.detail
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(defaults(), None, 4, 8, 0))
    assert "node is required" in e.value.detail


def test_capacity_bounds():
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.resolve_placement(defaults(), "tkamd1", 64, 8, 0))
    assert "cpu_cores must be between 1 and 16" in e.value.detail


def test_placement_read_back_from_hub_options():
    assert js._placement_of({"user_options": {"node": "tkspark", "cpu": "8", "memory": "32G", "enable_gpu": "1"}}) == {
        "node": "tkspark", "cpu_cores": 8, "memory_gb": 32, "gpus": 1,
    }
    assert js._placement_of(None) == {"node": None, "cpu_cores": None, "memory_gb": None, "gpus": None}
