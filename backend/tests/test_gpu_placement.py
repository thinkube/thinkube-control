#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An app container's GPUs come from the Scheduler: its manifest pins it to the node chosen for it, gives it its
GPUs by their ids from a ConfigMap Control writes, and asks Kubernetes for no GPU. A component's container asks for
none either: the LLM gateway starts its pods on the GPUs of its holds."""

import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import gpu_placement  # noqa: E402

GIB = 2**30
GPU = {"name": "inference", "build": ".", "port": 8000, "size": "xlarge", "gpu": {"count": 1, "memory": "20Gi"}}


def spec(container, kind="app"):
    return {"spec": {"containers": [container], "deployment": {"type": kind, "replicas": 1}}}


def card(index, total, used=0.0):
    return {"id": f"GPU-{index}", "index": index, "name": "RTX 3090", "memory_total_bytes": int(total * GIB),
            "memory_used_bytes": int(used * GIB), "memory_free_bytes": int((total - used) * GIB)}


NODES = [
    {"name": "tkamd1", "kind": "separate", "gpus": [card(0, 11)], "memory_total_bytes": 64 * GIB,
     "memory_available_bytes": 30 * GIB},
    {"name": "tkamd2", "kind": "separate", "gpus": [card(0, 24, 20), card(1, 24, 2)], "memory_total_bytes": 128 * GIB,
     "memory_available_bytes": 100 * GIB},
    {"name": "tkspark", "kind": "shared", "gpus": [{"id": "GPU-s", "index": 0, "name": "GB10"}],
     "memory_total_bytes": 121 * GIB, "memory_available_bytes": 60 * GIB},
]


def test_a_container_goes_to_the_node_whose_gpus_hold_it_with_the_most_free():
    # tkamd1's card is too small; tkamd2's card 1 has 22 GiB free, tkspark 60 GiB of the system's memory
    assert gpu_placement.choose(spec(GPU), "sd", {}, NODES) == {"inference": "tkspark"}
    on_cards = [n for n in NODES if n["kind"] == "separate"]
    assert gpu_placement.choose(spec(GPU), "sd", {}, on_cards) == {"inference": "tkamd2"}


def test_a_container_stays_on_its_node_while_that_node_holds_it():
    assert gpu_placement.choose(spec(GPU), "sd", {"inference": "tkamd2"}, NODES) == {"inference": "tkamd2"}
    # Its node can no longer hold it: it moves
    assert gpu_placement.choose(spec(GPU), "sd", {"inference": "tkamd1"}, NODES) == {"inference": "tkspark"}


def test_no_node_that_holds_it_is_an_error_that_names_the_nodes():
    big = {**GPU, "gpu": {"count": 2, "memory": "40Gi"}}
    with pytest.raises(gpu_placement.PlacementError) as e:
        gpu_placement.choose(spec(big), "sd", {}, NODES)
    assert "sd-inference needs 2 GPU(s) with 40 GiB each, and no node has them" in str(e.value)
    assert "tkamd2: RTX 3090 24 GiB, RTX 3090 24 GiB" in str(e.value)
    with pytest.raises(gpu_placement.PlacementError, match="written as <number>Gi"):
        gpu_placement.choose(spec({**GPU, "gpu": {"count": 1, "memory": "20GB"}}), "sd", {}, NODES)


def test_a_components_containers_and_containers_without_a_gpu_are_not_placed():
    assert gpu_placement.choose(spec(GPU, "component"), "vllm", {}, NODES) == {}
    assert gpu_placement.choose(spec({"name": "web", "build": "."}), "web", {}, NODES) == {}


def render(container, kind="app", gpu_nodes=None):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(REPO / "templates/k8s")),
                             undefined=jinja2.StrictUndefined, lstrip_blocks=True, trim_blocks=True)
    env.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    env.filters["to_json"] = lambda x: __import__("json").dumps(x)
    variables = dict(
        project_name="sd", k8s_namespace="sd", domain_name="thinkube.com", container_registry="registry.thinkube.com",
        admin_username="tkadmin", thinkube_spec=spec(container, kind), manifest_params={}, spec_env={},
        seaweedfs_endpoint="http://seaweedfs:8333", deployment_env_from=[], extension=False,
        system_username="thinkube", master_node_name="tkamd1")
    if gpu_nodes is not None:
        variables["gpu_nodes"] = gpu_nodes
    text = env.get_template("deployment-separate.j2").render(**variables)
    [deployment] = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "Deployment"]
    return deployment


def test_an_app_container_is_given_its_gpus_by_the_scheduler_on_its_node():
    deployment = render(GPU, gpu_nodes={"inference": "tkamd2"})
    assert deployment["metadata"]["annotations"] == {"scheduler.thinkube.io/gpus": "1",
                                                     "scheduler.thinkube.io/gpu-memory-gb": "20"}
    pod = deployment["spec"]["template"]
    assert pod["metadata"]["annotations"] == {"scheduler.thinkube.io/gpus": "1"}
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "tkamd2"}
    assert pod["spec"]["runtimeClassName"] == "nvidia-legacy"
    assert "affinity" not in pod["spec"]
    [container] = pod["spec"]["containers"]
    env = {e["name"]: e for e in container["env"]}
    assert env["NVIDIA_VISIBLE_DEVICES"]["valueFrom"] == {
        "configMapKeyRef": {"name": "sd-inference-gpus", "key": "NVIDIA_VISIBLE_DEVICES"}}
    assert env["NVIDIA_DRIVER_CAPABILITIES"]["value"] == "compute,utility"
    resources = container["resources"]
    assert "nvidia.com/gpu" not in resources.get("limits", {}) and "nvidia.com/gpu" not in resources.get("requests", {})


def test_an_app_container_with_a_gpu_needs_its_node():
    with pytest.raises(jinja2.UndefinedError):
        render(GPU, gpu_nodes={})


def test_a_components_container_asks_kubernetes_for_no_gpu():
    """The LLM gateway starts a component's pods on the GPUs of its holds; the base Deployment asks for none."""
    deployment = render(GPU, kind="component")
    pod = deployment["spec"]["template"]
    assert "annotations" not in deployment["metadata"] and "runtimeClassName" not in pod["spec"]
    [container] = pod["spec"]["containers"]
    assert "nvidia.com/gpu" not in container["resources"]["limits"]
    assert "NVIDIA_VISIBLE_DEVICES" not in {e["name"] for e in container["env"]}
