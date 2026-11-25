# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The nodes as the scheduler knows them, from what their node-metrics pods answer."""

import pytest

from app.services import nodes

GIB = 2**30


def gpu(index, uuid, name="NVIDIA GeForce RTX 3090", used=1532.0, total=24576.0, free=22596.0):
    return {"index": index, "uuid": uuid, "name": name, "utilization": 0.0, "memory_used_mb": used,
            "memory_total_mb": total, "memory_free_mb": free, "temp": 54.0, "power": 24.5}


def metrics(name, gpus, uma=False, error=None):
    return {"node_name": name, "is_uma": uma, "gpu_count": len(gpus), "gpus": gpus, "gpu_error": error,
            "memory_total_bytes": 125 * GIB, "memory_available_bytes": 114 * GIB}


def test_a_node_with_cards_has_each_gpu_by_its_id_with_memory_of_its_own():
    node = nodes.node_of(metrics("tkamd2", [gpu(1, "GPU-b", used=1.0, free=24126.0), gpu(0, "GPU-a")]))
    assert (node["name"], node["kind"]) == ("tkamd2", "separate")
    assert [g["id"] for g in node["gpus"]] == ["GPU-a", "GPU-b"]
    first = node["gpus"][0]
    assert (first["memory_total_bytes"], first["memory_used_bytes"]) == (24 * GIB, 1532 * 2**20)
    assert node["gpus"][1]["memory_free_bytes"] == 24126 * 2**20
    assert (node["memory_total_bytes"], node["memory_available_bytes"]) == (125 * GIB, 114 * GIB)


def test_a_gpu_that_shares_the_systems_memory_has_none_of_its_own():
    node = nodes.node_of(metrics("tkspark", [gpu(0, "GPU-s", "NVIDIA GB10", 0, 0, 0)], uma=True))
    assert node["kind"] == "shared"
    assert node["gpus"] == [{"id": "GPU-s", "index": 0, "name": "NVIDIA GB10", "utilization": 0.0,
                             "temperature": 54.0, "power": 24.5}]


def test_a_node_without_a_gpu_is_of_kind_none():
    assert nodes.node_of(metrics("tkamd1", []))["kind"] == "none"


def test_a_node_that_gives_no_gpu_id_or_cannot_read_its_gpus_is_an_error():
    old = gpu(0, "GPU-a")
    del old["uuid"]
    with pytest.raises(nodes.NodesError, match="gives no id for GPU 0"):
        nodes.node_of(metrics("tkamd2", [old]))
    with pytest.raises(nodes.NodesError, match="tkamd2 could not read its GPUs: nvidia-smi exited 9"):
        nodes.node_of(metrics("tkamd2", [], error="nvidia-smi exited 9"))


def test_the_nodes_are_listed_with_an_unreadable_one_in_its_place(signed_in, monkeypatch):
    class Fake:
        async def list(self):
            return [nodes.node_of(metrics("tkamd2", [gpu(0, "GPU-a")])), {"name": "tkspark", "error": "no answer"}]

    monkeypatch.setattr(nodes, "nodes", Fake())
    found = signed_in.get("/api/v1/nodes").json()["nodes"]
    assert [(n["name"], n.get("kind"), n.get("error")) for n in found] == [("tkamd2", "separate", None),
                                                                           ("tkspark", None, "no answer")]


def test_nodes_that_cannot_be_listed_are_a_502_with_the_reason(signed_in, monkeypatch):
    class Fake:
        async def list(self):
            raise nodes.NodesError("no node-metrics pod is running in thinkube-control")

    monkeypatch.setattr(nodes, "nodes", Fake())
    answer = signed_in.get("/api/v1/nodes")
    assert answer.status_code == 502 and "no node-metrics pod" in answer.json()["detail"]


def test_a_node_is_given_a_limit_of_gpus_at_work_and_loses_it(signed_in, monkeypatch):
    class Fake:
        async def list(self):
            return [nodes.node_of(metrics("tkamd2", [gpu(0, "GPU-a")]))]

    monkeypatch.setattr(nodes, "nodes", Fake())
    assert signed_in.get("/api/v1/nodes").json()["nodes"][0]["gpus_at_work"] is None
    assert signed_in.put("/api/v1/nodes/tkamd2/limit", json={"gpus_at_work": 1}).json() == {
        "node": "tkamd2", "gpus_at_work": 1}
    assert signed_in.get("/api/v1/nodes").json()["nodes"][0]["gpus_at_work"] == 1
    signed_in.put("/api/v1/nodes/tkamd2/limit", json={"gpus_at_work": None})
    assert signed_in.get("/api/v1/nodes").json()["nodes"][0]["gpus_at_work"] is None
