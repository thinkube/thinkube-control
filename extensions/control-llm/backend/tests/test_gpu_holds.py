# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A model's pod is started on the GPUs the Scheduler holds for it, gives them up for work of a higher priority, and
is started again when the Scheduler gives them back."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from kubernetes import client

from app.api.llm.schemas import ModelEntry, ModelState
from app.services import gpu_holds
from app.services.llm_lifecycle import LLMLifecycleManager
from app.services.llm_model_registry import LLMModelRegistry
from app.services.llm_pod_manager import LLMPodManager, model_slug

MODEL = "Qwen/Qwen3-Embedding-0.6B"
BACKEND = f"vllm-tkamd2/{model_slug(MODEL)}"


@pytest.fixture
def world(monkeypatch):
    """A registry with one model loading on tkamd2, and the pod manager's calls recorded."""
    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    entry = ModelEntry(id=MODEL, name="embeddings", server_type=["vllm"], serving_name=MODEL,
                       state=ModelState.loading, backend_id=BACKEND)
    registry._models = {MODEL: entry}

    import app.services.llm_gpu_tracker as trk
    import app.services.llm_model_registry as reg
    import app.services.llm_pod_manager as pods

    released = []
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(
        release_allocation=released.append,
        memory_type=lambda node: "uma" if node == "tkspark" else "discrete"))
    monkeypatch.setattr(reg, "llm_model_registry", registry)
    made, deleted, given_up = [], [], []

    async def ensure_pod(backend_type, node, **kwargs):
        made.append((node, kwargs["gpu_ids"], kwargs["hold_id"]))
        return True, None

    async def delete_pod(backend_type, node, model_id=None):
        deleted.append(model_id)
        return True

    state = SimpleNamespace(kept="hold-1")

    def give_up(backend_type, node, model_id=None):
        given_up.append((node, model_id))
        return state.kept

    monkeypatch.setattr(pods, "llm_pod_manager", SimpleNamespace(ensure_pod=ensure_pod, delete_pod=delete_pod,
                                                                give_up=give_up))
    return SimpleNamespace(registry=registry, entry=entry, made=made, deleted=deleted, given_up=given_up,
                           released=released, state=state)


def load(estimated=3.0, node="tkamd2", gpus=1):
    payload = {"model_id": MODEL, "task": "feature-extraction"}
    asyncio.run(LLMLifecycleManager()._load_performance_background(MODEL, "vllm", node, gpus, payload, estimated))


def test_a_models_pod_is_started_on_the_gpus_its_hold_was_given(world, holds):
    holds.gpu_ids = ["GPU-b"]
    load(estimated=3.0)
    assert holds.asked == [{"name": MODEL, "node": "tkamd2", "gpus": 1, "gpu_memory_gb": 3.0, "memory_gb": 0,
                            "on_gpus": None}]
    assert world.made == [("tkamd2", ["GPU-b"], "hold-1")]
    assert world.entry.state == ModelState.loading


def test_the_hold_says_the_models_estimated_memory(world, holds):
    # On a node whose GPU takes its memory from the system's, the estimate is the whole footprint
    world.entry.backend_id = f"vllm-tkspark/{model_slug(MODEL)}"
    load(estimated=31.5, node="tkspark")
    # On GPUs with memory of their own, it is spread over the pod's GPUs
    world.entry.backend_id = BACKEND
    load(estimated=30.0, gpus=2)
    assert [(a["gpus"], a["gpu_memory_gb"]) for a in holds.asked] == [(1, 31.5), (2, 15.0)]


def test_a_load_the_scheduler_gives_no_gpu_is_refused_with_its_reason(world, holds):
    holds.refuse = "waiting for a GPU of tkamd2 (3.0 GiB on a GPU): GPU 0 has 1.2 GiB free; GPU 1 has 0.8 GiB free"
    load()
    assert world.made == []
    assert world.entry.state == ModelState.deployable
    assert world.entry.last_error == f"the Scheduler gave it no GPU: {holds.refuse}"


def test_a_model_gives_its_gpus_up_and_keeps_its_deployment_to_start_again(world, holds):
    world.entry.state = ModelState.available
    holds.status = "stopping"
    said = asyncio.run(LLMLifecycleManager().give_up("hold-1", MODEL, "training of run 7"))
    assert world.given_up == [("tkamd2", MODEL)] and holds.released == ["hold-1"] and holds.ended == []
    assert world.entry.state == ModelState.deployable and world.released == [MODEL]
    assert world.entry.last_error == ("unloaded to make room for training of run 7; it is loaded again when its "
                                      "GPUs are free")
    assert said == f"{MODEL} was unloaded from tkamd2, and is loaded again when its GPUs are free"
    # The hold waits again: asked once more, nothing is done
    holds.status = "waiting"
    asyncio.run(LLMLifecycleManager().give_up("hold-1", MODEL, "training of run 7"))
    assert world.given_up == [("tkamd2", MODEL)] and holds.released == ["hold-1"]


def test_a_hold_no_pod_of_the_gateway_is_on_is_ended(world, holds):
    holds.status = "stopping"
    # The model's pod is on another hold's GPUs
    world.state.kept = "hold-9"
    asyncio.run(LLMLifecycleManager().give_up("hold-1", MODEL, "a job"))
    # The gateway knows no such model
    asyncio.run(LLMLifecycleManager().give_up("hold-2", "no/such-model", "a job"))
    assert holds.ended == ["hold-1", "hold-2"] and holds.released == []


# --- the Deployments ---------------------------------------------------------

def base_deployment():
    container = client.V1Container(
        name="inference", image="registry/vllm:1",
        env=[client.V1EnvVar(name="NVIDIA_VISIBLE_DEVICES", value="all")],
        resources=client.V1ResourceRequirements(limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                                                requests={"cpu": "1", "memory": "4Gi"}))
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name="vllm-inference"),
        spec=client.V1DeploymentSpec(
            replicas=0, selector=client.V1LabelSelector(match_labels={"app": "vllm"}),
            template=client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels={"app": "vllm"}),
                                              spec=client.V1PodSpec(containers=[container]))))


@pytest.fixture
def cluster(monkeypatch):
    """The Deployments of the namespace vllm, as the pod manager reads and changes them."""
    import kubernetes

    state = SimpleNamespace(deployments={"vllm-inference": base_deployment(), "ollama": base_deployment()},
                            patches=[], deleted=[])
    bases = ("vllm-inference", "ollama")

    class Apps:
        def read_namespaced_deployment(self, name, ns):
            if name not in state.deployments:
                raise kubernetes.client.rest.ApiException(status=404)
            # A read gives the Deployment as it is stored, not the stored object
            return copy.deepcopy(state.deployments[name])

        def create_namespaced_deployment(self, ns, deployment):
            state.deployments[deployment.metadata.name] = deployment

        def delete_namespaced_deployment(self, name, ns):
            state.deleted.append(name)
            del state.deployments[name]

        def patch_namespaced_deployment(self, name, ns, patch):
            state.patches.append((name, patch))

        def list_namespaced_deployment(self, ns, label_selector):
            return SimpleNamespace(items=[d for name, d in state.deployments.items()
                                          if name not in bases and d.metadata.namespace == ns])

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "AppsV1Api", Apps)
    return state


def test_a_deployment_on_a_holds_gpus_asks_kubernetes_for_none(cluster):
    manager = LLMPodManager()
    assert manager._create_node_deployment("vllm", "tkamd2", 2, {"MODEL_ID": MODEL}, None, MODEL,
                                           ["GPU-a", "GPU-b"], "hold-1")
    made = cluster.deployments[manager._make_deployment_name("vllm", "tkamd2", MODEL)]
    spec = made.spec.template.spec
    container = spec.containers[0]
    assert spec.runtime_class_name == "nvidia-legacy"
    assert [e.value for e in container.env if e.name == "NVIDIA_VISIBLE_DEVICES"] == ["GPU-a,GPU-b"]
    assert "nvidia.com/gpu" not in container.resources.limits
    assert "nvidia.com/gpu" not in (container.resources.requests or {})
    assert made.metadata.annotations[gpu_holds.HOLD_ANNOTATION] == "hold-1"
    # On several GPUs its ranks get shared memory, as before
    assert any(v.name == "dshm" for v in spec.volumes)


def test_the_hold_ends_with_the_deployment_and_a_new_hold_makes_it_again(cluster, holds):
    manager = LLMPodManager()
    name = manager._make_deployment_name("vllm", "tkamd2", MODEL)
    manager._create_node_deployment("vllm", "tkamd2", 1, {"MODEL_ID": MODEL}, None, MODEL, ["GPU-a"], "hold-1")
    # Loaded again with a new hold, on other GPUs: the Deployment is made again, and the old hold ended
    manager._create_node_deployment("vllm", "tkamd2", 1, {"MODEL_ID": MODEL}, None, MODEL, ["GPU-b"], "hold-2")
    assert cluster.deleted == [name] and holds.ended == ["hold-1"]
    env = cluster.deployments[name].spec.template.spec.containers[0].env
    assert [e.value for e in env if e.name == "NVIDIA_VISIBLE_DEVICES"] == ["GPU-b"]
    assert manager._delete_deployment("vllm", name)
    assert holds.ended == ["hold-1", "hold-2"] and name not in cluster.deployments


def test_a_deployment_scaled_to_zero_ends_its_hold(cluster, holds):
    manager = LLMPodManager()
    name = manager._make_deployment_name("vllm", "tkamd2", MODEL)
    manager._create_node_deployment("vllm", "tkamd2", 1, {"MODEL_ID": MODEL}, None, MODEL, ["GPU-a"], "hold-1")
    assert manager.scale_to_zero("vllm", "tkamd2", MODEL)
    assert holds.ended == ["hold-1"]
    assert cluster.patches == [(name, {"spec": {"replicas": 0}, "metadata": {"annotations": {
        gpu_holds.HOLD_ANNOTATION: None, gpu_holds.RELOAD_ANNOTATION: None, gpu_holds.MODELS_ANNOTATION: None}}})]


def test_a_deployment_that_gave_its_gpus_up_starts_again_when_its_hold_has_gpus(cluster, holds):
    manager = LLMPodManager()
    name = manager._make_deployment_name("vllm", "tkamd2", MODEL)
    manager._create_node_deployment("vllm", "tkamd2", 1, {"MODEL_ID": MODEL}, None, MODEL, ["GPU-a"], "hold-1")
    assert manager.give_up("vllm", "tkamd2", MODEL) == "hold-1"
    assert holds.ended == []
    assert cluster.patches == [(name, {"spec": {"replicas": 0},
                                       "metadata": {"annotations": {gpu_holds.RELOAD_ANNOTATION: "true"}}})]
    made = cluster.deployments[name]
    made.metadata.annotations[gpu_holds.RELOAD_ANNOTATION] = "true"
    made.spec.replicas = 0
    cluster.patches.clear()
    # Its hold waits for its turn: nothing starts
    holds.status = "waiting"
    assert manager.start_again() == [] and cluster.patches == []
    # The hold has GPUs again, other ones: the pod starts on them
    holds.status, holds.gpu_ids = "running", ["GPU-b"]
    assert manager.start_again() == [{"model": MODEL}]
    assert cluster.patches == [(name, {
        "metadata": {"annotations": {gpu_holds.RELOAD_ANNOTATION: None, gpu_holds.MODELS_ANNOTATION: None}},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [
            {"name": "inference", "env": [{"name": "NVIDIA_VISIBLE_DEVICES", "value": "GPU-b"}]}]}}}})]
    # A hold someone ended starts nothing more: the Deployment's marks are removed
    cluster.patches.clear()
    holds.status = "stopped"
    assert manager.start_again() == []
    assert cluster.patches == [(name, {"metadata": {"annotations": {gpu_holds.HOLD_ANNOTATION: None,
                                                                    gpu_holds.RELOAD_ANNOTATION: None,
                                                                    gpu_holds.MODELS_ANNOTATION: None}}})]
    # A Deployment without a hold gives nothing up
    made.metadata.annotations = {}
    assert manager.give_up("vllm", "tkamd2", MODEL) is None


def test_a_hold_no_deployment_is_on_is_ended_once_it_is_old(cluster, holds):
    from datetime import datetime, timedelta, timezone

    manager = LLMPodManager()
    manager._create_node_deployment("vllm", "tkamd2", 1, {"MODEL_ID": MODEL}, None, MODEL, ["GPU-a"], "hold-1")
    now = datetime.now(timezone.utc)
    holds.listed = [
        {"id": "hold-1", "name": MODEL, "created_at": (now - timedelta(hours=2)).isoformat()},
        {"id": "hold-2", "name": "asked a moment ago", "created_at": (now - timedelta(minutes=1)).isoformat()},
        {"id": "hold-3", "name": "left behind", "created_at": (now - timedelta(hours=2)).isoformat()},
    ]
    assert manager.end_orphan_holds() == ["hold-3"] and holds.ended == ["hold-3"]


# --- a pod that serves several models -----------------------------------------

def test_a_pod_of_several_models_keeps_its_models_when_it_gives_its_gpus_up(cluster, holds):
    manager = LLMPodManager()
    name = manager._make_deployment_name("ollama", "tkspark", None)
    assert manager.shared_pod("ollama", "tkspark") == (False, None)
    manager._create_node_deployment("ollama", "tkspark", 1, None, None, None, ["GPU-s"], "hold-1")
    assert manager.shared_pod("ollama", "tkspark") == (True, "hold-1")
    assert manager.shared_holds() == [("ollama", "tkspark", "hold-1")]
    assert manager.give_up("ollama", "tkspark", None, ["a/one", "b/two"]) == "hold-1"
    assert cluster.patches == [(name, {"spec": {"replicas": 0}, "metadata": {"annotations": {
        gpu_holds.RELOAD_ANNOTATION: "true", gpu_holds.MODELS_ANNOTATION: '["a/one", "b/two"]'}}})]
    made = cluster.deployments[name]
    made.metadata.annotations.update({gpu_holds.RELOAD_ANNOTATION: "true",
                                      gpu_holds.MODELS_ANNOTATION: '["a/one", "b/two"]'})
    made.spec.replicas = 0
    # Stopped, it is no running pod, and asked again it keeps the models it kept
    assert manager.shared_pod("ollama", "tkspark") == (False, None) and manager.shared_holds() == []
    cluster.patches.clear()
    manager.give_up("ollama", "tkspark", None, [])
    assert cluster.patches == [(name, {"spec": {"replicas": 0},
                                       "metadata": {"annotations": {gpu_holds.RELOAD_ANNOTATION: "true"}}})]
    holds.gpu_ids = ["GPU-s"]
    assert manager.start_again() == [{"backend_type": "ollama", "node": "tkspark", "models": ["a/one", "b/two"]}]


def test_a_pod_on_a_hold_found_at_a_further_load_is_not_made_again(cluster, holds, monkeypatch):
    import app.services.llm_gpu_tracker as trk

    # The node as the gateway knows it: one GPU, asked of Kubernetes as one slot
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(get_node=lambda node: SimpleNamespace(
        gpu_count=1, gpu_replicas=1, total_slots=1)))
    monkeypatch.setattr(trk, "gpu_slots", lambda node, gpus: gpus)
    manager = LLMPodManager()
    monkeypatch.setattr(manager, "check_pod_status", lambda *a: ("running", ""))
    monkeypatch.setattr(manager, "_sync_deployment_image", lambda *a: None)
    name = manager._make_deployment_name("ollama", "tkspark", None)
    manager._create_node_deployment("ollama", "tkspark", 1, None, None, None, ["GPU-s"], "hold-1")
    # One more model is loaded on the pod: its Deployment is found, with no GPUs given this time
    assert manager._create_node_deployment("ollama", "tkspark", 1, None, None, None)
    assert cluster.deleted == [] and holds.ended == []
    env = cluster.deployments[name].spec.template.spec.containers[0].env
    assert [e.value for e in env if e.name == "NVIDIA_VISIBLE_DEVICES"] == ["GPU-s"]
    # Stopped for work of a higher priority, it is not started by a load: it waits for its hold
    cluster.deployments[name].spec.replicas = 0
    assert not manager._create_node_deployment("ollama", "tkspark", 1, None, None, None)
    assert cluster.deleted == [] and cluster.patches == []


@pytest.fixture
def shared(monkeypatch):
    """The trained heads' pod on tkspark, as the lifecycle sees it: whether it runs and on which hold, and the
    models allocated on it."""
    import app.services.llm_gpu_tracker as trk
    import app.services.llm_model_registry as reg
    import app.services.llm_pod_manager as pods

    registry = LLMModelRegistry.__new__(LLMModelRegistry)
    registry._models = {m: ModelEntry(id=m, name=m, server_type=["heads"], serving_name=m,
                                      state=ModelState.available, backend_id="heads-tkspark")
                        for m in ("kubexlat/ja-seg", "kubexlat/ja-parse")}
    state = SimpleNamespace(pod=(False, None), given_up=[], released=[], kept="hold-1")
    allocations = [SimpleNamespace(model_id=m, backend_id="heads-tkspark", estimated_memory_gb=1.6)
                   for m in registry._models]
    monkeypatch.setattr(trk, "llm_gpu_tracker", SimpleNamespace(
        _allocations={"tkspark": allocations}, memory_type=lambda node: "uma",
        release_allocation=state.released.append))
    monkeypatch.setattr(reg, "llm_model_registry", registry)

    def give_up(backend_type, node, model_id=None, models=None):
        state.given_up.append((backend_type, node, models))
        return state.kept

    monkeypatch.setattr(pods, "llm_pod_manager", SimpleNamespace(
        shared_pod=lambda backend_type, node: state.pod, give_up=give_up))
    state.registry = registry
    return state


def test_the_first_model_of_a_pod_asks_for_its_hold_and_the_next_ones_for_more_memory(shared, holds):
    manager = LLMLifecycleManager()
    # No pod runs: a hold for the pod, named after it, with the first model's memory
    holds.gpu_ids = ["GPU-s"]
    assert asyncio.run(manager._shared_pod_gpus("heads", "tkspark", 1, 4.6)) == {"gpu_ids": ["GPU-s"],
                                                                                "hold_id": "hold-1"}
    assert holds.asked == [{"name": "heads-tkspark", "node": "tkspark", "gpus": 1, "gpu_memory_gb": 4.6,
                            "memory_gb": 0, "on_gpus": None}]
    # The pod runs on its hold: the hold grows to the two models on it and the new one, before the load
    shared.pod = (True, "hold-1")
    assert asyncio.run(manager._shared_pod_gpus("heads", "tkspark", 1, 1.6)) == {}
    assert holds.resized == [("hold-1", 4.8)]
    # The Scheduler refuses when the GPU does not hold the model too
    holds.refuse_resize = "waiting for 40.0 GiB of memory on tkspark"
    with pytest.raises(gpu_holds.HoldError, match="waiting for 40.0 GiB of memory on tkspark"):
        asyncio.run(manager._shared_pod_gpus("heads", "tkspark", 1, 36.8))
    # A pod that runs without a hold asked Kubernetes for its GPU, and stays so
    shared.pod = (True, None)
    assert asyncio.run(manager._shared_pod_gpus("heads", "tkspark", 1, 1.6)) == {}
    assert len(holds.asked) == 1 and len(holds.resized) == 1


def test_a_pod_of_several_models_gives_its_gpu_up_with_its_models(shared, holds, llm_backend_types_installed,
                                                                 monkeypatch):
    from app.services.llm_backend_types import BackendType, llm_backend_types

    monkeypatch.setattr(llm_backend_types, "_types", {**llm_backend_types_installed, "heads": BackendType(
        type="heads", display_name="Trained heads", namespace="heads", deployment="heads", port=7860,
        api_path="/v1", health_path="/health", driver="shared-pod", tier="performance")})
    holds.status = "stopping"
    said = asyncio.run(LLMLifecycleManager().give_up("hold-1", "heads-tkspark", "training of run 7"))
    assert shared.given_up == [("heads", "tkspark", ["kubexlat/ja-seg", "kubexlat/ja-parse"])]
    assert holds.released == ["hold-1"] and shared.released == ["kubexlat/ja-seg", "kubexlat/ja-parse"]
    assert all(e.state == ModelState.deployable and "loaded again when its GPUs are free" in e.last_error
               for e in shared.registry._models.values())
    assert said == ("the heads pod on tkspark was stopped with 2 models, which are loaded again when its GPUs are "
                    "free")
