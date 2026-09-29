# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""GPU details come from each node's node-metrics pod; time-slicing from node labels."""

import json
from types import SimpleNamespace

import httpx
import pytest

import app.api.cluster_resources as cr

RTX_3090_METRICS = {
    "is_uma": False,
    "gpu_error": None,
    "gpus": [
        {"index": 0, "name": "NVIDIA GeForce RTX 3090", "utilization": 0.0,
         "memory_used_mb": 1.0, "memory_total_mb": 24576.0, "memory_free_mb": 24126.0},
        {"index": 1, "name": "NVIDIA GeForce RTX 3090", "utilization": 80.0,
         "memory_used_mb": 20000.0, "memory_total_mb": 24576.0, "memory_free_mb": 4576.0},
    ],
}

GB10_METRICS = {
    "is_uma": True,
    "gpu_error": None,
    "gpus": [
        {"index": 0, "name": "NVIDIA GB10", "utilization": 1.0,
         "memory_used_mb": 0.0, "memory_total_mb": 0.0, "memory_free_mb": 0.0},
    ],
}


def _serve(monkeypatch, payload, status=200):
    def fake_get(url, timeout):
        request = httpx.Request("GET", url)
        return httpx.Response(status, json=payload, request=request)

    monkeypatch.setattr(cr.httpx, "get", fake_get)


def test_details_are_the_physical_gpus(monkeypatch):
    _serve(monkeypatch, RTX_3090_METRICS)
    gpus = cr._get_gpu_details("tkamd2", "10.0.0.2")
    assert [g["model"] for g in gpus] == ["NVIDIA GeForce RTX 3090"] * 2
    assert gpus[0]["memory_total"] == "24576 MiB"
    assert gpus[0]["available"] is True
    assert gpus[1]["available"] is False


def test_unified_memory_gpu(monkeypatch):
    _serve(monkeypatch, GB10_METRICS)
    gpus = cr._get_gpu_details("tkspark", "10.0.0.3")
    assert gpus[0]["model"] == "NVIDIA GB10"
    assert gpus[0]["memory_total"] == "unified"


def test_no_node_metrics_pod_is_an_error():
    with pytest.raises(cr.GPUDetailsError, match="no running node-metrics pod"):
        cr._get_gpu_details("tkamd2", None)


def test_nvidia_smi_failure_is_an_error(monkeypatch):
    _serve(monkeypatch, {"gpus": [], "gpu_error": "nvidia-smi exited 9: driver mismatch"})
    with pytest.raises(cr.GPUDetailsError, match="driver mismatch"):
        cr._get_gpu_details("tkamd2", "10.0.0.2")


def test_unreachable_node_metrics_is_an_error(monkeypatch):
    _serve(monkeypatch, {"error": "boom"}, status=500)
    with pytest.raises(cr.GPUDetailsError, match="did not answer"):
        cr._get_gpu_details("tkamd2", "10.0.0.2")


class _Node:
    def __init__(self, name, gpu, labels):
        self.metadata = SimpleNamespace(name=name, labels=labels)
        self.status = SimpleNamespace(capacity={"cpu": "32", "memory": "128Gi", "nvidia.com/gpu": str(gpu)})


class _FakeV1:
    def __init__(self, nodes, pods):
        self._nodes = nodes
        self._pods = pods

    def list_node(self):
        return SimpleNamespace(items=self._nodes)

    def list_pod_for_all_namespaces(self, **kwargs):
        return SimpleNamespace(data=json.dumps({"items": self._pods}))


def _node_metrics_pod(node, ip):
    return {
        "metadata": {"namespace": "thinkube-control", "labels": {"app": "node-metrics"}},
        "spec": {"nodeName": node, "containers": []},
        "status": {"phase": "Running", "podIP": ip},
    }


def test_time_sliced_node_is_capped_at_one_gpu_even_without_details(monkeypatch):
    nodes = [
        _Node("tkamd2", 4, {"nvidia.com/gpu.replicas": "2", "nvidia.com/gpu.count": "2"}),
        _Node("plain", 2, {}),
        _Node("cpu", 0, {}),
    ]
    pods = [_node_metrics_pod("plain", "10.0.0.9")]
    monkeypatch.setattr(cr.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(cr.client, "CoreV1Api", lambda: _FakeV1(nodes, pods))
    _serve(monkeypatch, RTX_3090_METRICS)

    result = {n["name"]: n for n in cr._compute_cluster_resources()}

    assert result["tkamd2"]["capacity"]["effective_gpu"] == 1
    assert result["tkamd2"]["gpu_details"] == []
    assert "no running node-metrics pod" in result["tkamd2"]["gpu_error"]

    assert result["plain"]["capacity"]["effective_gpu"] == 2
    assert len(result["plain"]["gpu_details"]) == 2
    assert result["plain"]["gpu_error"] is None

    assert result["cpu"]["capacity"]["effective_gpu"] == 0
    assert result["cpu"]["gpu_error"] is None
