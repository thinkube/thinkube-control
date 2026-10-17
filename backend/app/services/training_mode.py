# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Training mode: the whole cluster reserved for one training.

Turning it on unloads every served model, scales to 0 every Deployment and
StatefulSet outside the namespaces a training needs (KEEP_NAMESPACES), marks
the training node so no new pod is placed there, and moves the pods of kept
Deployments off that node. Data stays where it is: a workload at 0 replicas
keeps its volumes.

What is stopped is found when training mode is turned on, not listed in
advance, and kept in one record (a ConfigMap of thinkube-control): the
models that were loaded and on which node, and every workload with its
replica count. Turning training mode off restores exactly that record and
nothing else.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

RECORD_NAMESPACE = "thinkube-control"
RECORD_NAME = "training-mode"

# The taint that keeps new pods off the training node; the training job's
# pod is already there when training mode is turned on.
TRAINING_TAINT = "thinkube.io/training"

# What a training needs, by namespace:
KEEP_NAMESPACES = frozenset({
    # the cluster itself: network, DNS, volume drivers, GPUs
    "kube-system", "kube-public", "kube-node-lease", "cilium-secrets", "dns-system", "gpu-operator",
    # the volumes of notebooks, models, data and checkpoints (JuiceFS, its metadata, its data)
    "juicefs", "valkey", "seaweedfs",
    # the training job's server, and the platform that runs the job and ends training mode
    "jupyterhub", "thinkube-control", "postgres",
    # sign-in, the job's image, and the HTTPS routes and remote access to all of them
    "keycloak", "registry", "envoy-gateway-system", "gateway-system", "tailscale",
    # the person's IDE, and the scoring of the training's milestones
    "code-server", "scoring",
})

# Kept namespaces whose pods stay on the training node: node daemons, the
# job's own server and proxy, and the remote-access proxies.
STAY_ON_NODE = frozenset({"kube-system", "gpu-operator", "jupyterhub", "tailscale"})

# How long a controller still running may take to bring a workload back
# before the scale to 0 is repeated, and how many times.
SETTLE_SECONDS = 5
SETTLE_ROUNDS = 12


class TrainingModeError(Exception):
    """Training mode could not do what was asked; the message says what."""


def _apis():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------- the record

def _read_record(core) -> Optional[Dict[str, Any]]:
    try:
        cm = core.read_namespaced_config_map(RECORD_NAME, RECORD_NAMESPACE)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    return json.loads(cm.data["record"])


def _write_record(core, record: Dict[str, Any], create: bool) -> None:
    body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=RECORD_NAME, namespace=RECORD_NAMESPACE),
        data={"record": json.dumps(record, indent=1)},
    )
    if create:
        core.create_namespaced_config_map(RECORD_NAMESPACE, body)
    else:
        core.replace_namespaced_config_map(RECORD_NAME, RECORD_NAMESPACE, body)


# ------------------------------------------------------------------ workloads

def _scalable(apps) -> List[Dict[str, Any]]:
    """Every Deployment and StatefulSet outside KEEP_NAMESPACES with replicas above 0."""
    found = []
    for kind, items in (
        ("deployment", apps.list_deployment_for_all_namespaces().items),
        ("statefulset", apps.list_stateful_set_for_all_namespaces().items),
    ):
        for w in items:
            if w.metadata.namespace in KEEP_NAMESPACES:
                continue
            if (w.spec.replicas or 0) > 0:
                found.append({"namespace": w.metadata.namespace, "kind": kind,
                              "name": w.metadata.name, "replicas": w.spec.replicas})
    return found


def _scale(apps, w: Dict[str, Any], replicas: int) -> None:
    body = {"spec": {"replicas": replicas}}
    if w["kind"] == "deployment":
        apps.patch_namespaced_deployment(w["name"], w["namespace"], body)
    else:
        apps.patch_namespaced_stateful_set(w["name"], w["namespace"], body)


def _scale_to_zero(apps, w: Dict[str, Any]) -> bool:
    """Scale one workload to 0. False when it no longer exists: one deleted
    since it was listed has nothing left to stop."""
    try:
        _scale(apps, w, 0)
    except ApiException as e:
        if e.status == 404:
            return False
        raise
    return True


def _current_replicas(apps, w: Dict[str, Any]) -> Optional[int]:
    try:
        if w["kind"] == "deployment":
            return apps.read_namespaced_deployment(w["name"], w["namespace"]).spec.replicas or 0
        return apps.read_namespaced_stateful_set(w["name"], w["namespace"]).spec.replicas or 0
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def _stop_all(apps, record: Dict[str, Any], core) -> List[str]:
    """Scale every recorded workload to 0, then again whatever a controller
    brings back or creates, until nothing outside the kept namespaces runs.
    Returns what is still running after the last round."""
    known = {(w["namespace"], w["kind"], w["name"]) for w in record["workloads"]}
    for w in list(record["workloads"]):
        if not _scale_to_zero(apps, w):
            record["workloads"].remove(w)
    left: List[Dict[str, Any]] = []
    for _ in range(SETTLE_ROUNDS):
        time.sleep(SETTLE_SECONDS)
        left = _scalable(apps)
        if not left:
            return []
        for w in left:
            key = (w["namespace"], w["kind"], w["name"])
            if key not in known and _scale_to_zero(apps, w):
                record["workloads"].append(w)
                known.add(key)
            elif key in known:
                _scale_to_zero(apps, w)
        _write_record(core, record, create=False)
    return [f"{w['kind']} {w['namespace']}/{w['name']}" for w in left]


# ----------------------------------------------------------------------- node

def _taint(core, node: str, on: bool) -> None:
    n = core.read_node(node)
    taints = [t for t in (n.spec.taints or []) if t.key != TRAINING_TAINT]
    if on:
        taints.append(client.V1Taint(key=TRAINING_TAINT, value="true", effect="NoSchedule"))
    core.patch_node(node, {"spec": {"taints": [t.to_dict() for t in taints] or None}})


def _move_kept_pods(core, node: str) -> List[str]:
    """Evict the pods of kept Deployments from the training node, so they are
    placed again elsewhere; StatefulSets keep their pods, which may hold local data."""
    moved = []
    pods = core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node}").items
    for p in pods:
        ns = p.metadata.namespace
        if ns not in KEEP_NAMESPACES or ns in STAY_ON_NODE:
            continue
        owners = p.metadata.owner_references or []
        if not any(o.kind == "ReplicaSet" for o in owners):
            continue
        core.create_namespaced_pod_eviction(
            p.metadata.name, ns,
            client.V1Eviction(metadata=client.V1ObjectMeta(name=p.metadata.name, namespace=ns)),
        )
        moved.append(f"{ns}/{p.metadata.name}")
    return moved


# --------------------------------------------------------------------- models

async def _loaded_models() -> List[Dict[str, str]]:
    from app.services.llm_gpu_tracker import llm_gpu_tracker

    status = await llm_gpu_tracker.get_status()
    return [{"model_id": a.model_id, "node": n.name} for n in status.nodes for a in n.allocations]


async def _unload(models: List[Dict[str, str]]) -> List[str]:
    from app.services.llm_lifecycle import llm_lifecycle

    refused = []
    for m in models:
        r = await llm_lifecycle.unload_model(m["model_id"], False)
        if r.state.value != "deployable":
            refused.append(f"{m['model_id']}: {r.state.value} — {r.message}")
    return refused


# A model load takes minutes; a node starts one model at a time.
LOAD_SECONDS = 30 * 60
LOAD_POLL_SECONDS = 10


# How long the restored workloads may take to be ready before the models load.
READY_SECONDS = 15 * 60


def _not_ready(apps, workloads: List[Dict[str, Any]]) -> List[str]:
    waiting = []
    for w in workloads:
        try:
            obj = (apps.read_namespaced_deployment if w["kind"] == "deployment"
                   else apps.read_namespaced_stateful_set)(w["name"], w["namespace"])
        except ApiException as e:
            if e.status == 404:
                continue
            raise
        if (obj.status.ready_replicas or 0) < (obj.spec.replicas or 0):
            waiting.append(f"{w['kind']} {w['namespace']}/{w['name']}")
    return waiting


async def _load_one_after_another(apps, restored: List[Dict[str, Any]], models: List[Dict[str, str]]) -> None:
    """Load the models again in order, each once the one before it is
    available or has failed: a node refuses a load while another starts.
    The loads begin once the restored workloads are ready: a serving pod
    looks its model up in MLflow when it starts."""
    from app.services.llm_lifecycle import llm_lifecycle
    from app.services.llm_model_registry import llm_model_registry

    waited, waiting = 0, await asyncio.to_thread(_not_ready, apps, restored)
    while waiting and waited < READY_SECONDS:
        await asyncio.sleep(LOAD_POLL_SECONDS)
        waited += LOAD_POLL_SECONDS
        waiting = await asyncio.to_thread(_not_ready, apps, restored)
    if waiting:
        logger.error("training mode off: not ready after %d s, the models load anyway: %s", READY_SECONDS, waiting)

    for m in models:
        r = await llm_lifecycle.load_model(m["model_id"], node=m["node"])
        if r.state.value != "loading":
            logger.error("training mode off: %s on %s was not loaded: %s — %s",
                         m["model_id"], m["node"], r.state.value, r.message)
            continue
        waited = 0
        while waited < LOAD_SECONDS:
            await asyncio.sleep(LOAD_POLL_SECONDS)
            waited += LOAD_POLL_SECONDS
            entry = llm_model_registry.get_model(m["model_id"])
            if entry.state.value == "available":
                logger.info("training mode off: %s is available on %s", m["model_id"], m["node"])
                break
            if entry.state.value == "deployable":
                logger.error("training mode off: the load of %s on %s failed", m["model_id"], m["node"])
                break
        else:
            logger.error("training mode off: %s on %s was not available after %d s",
                         m["model_id"], m["node"], LOAD_SECONDS)


# ----------------------------------------------------------------- public API

async def status() -> Optional[Dict[str, Any]]:
    core, _ = _apis()
    return await asyncio.to_thread(_read_record, core)


async def turn_on(node: str, reason: str) -> Dict[str, Any]:
    core, apps = _apis()
    current = await asyncio.to_thread(_read_record, core)
    if current is not None:
        raise TrainingModeError(
            f"training mode is already on since {current['started']} on {current['node']} "
            f"({current['reason']}); turn it off first"
        )
    try:
        await asyncio.to_thread(core.read_node, node)
    except ApiException as e:
        raise TrainingModeError(f"node {node} cannot be read: {e.reason}")

    models = await _loaded_models()
    record = {"node": node, "reason": reason, "started": _now(), "models": models, "workloads": []}
    # The record is written before anything is stopped, so a failure part way
    # leaves what is needed to turn training mode off again.
    await asyncio.to_thread(_write_record, core, record, True)

    await asyncio.to_thread(_taint, core, node, True)
    # Unloading takes down each model's serving Deployment, so the workloads
    # are listed after it.
    refused = await _unload(models)
    record["workloads"] = await asyncio.to_thread(_scalable, apps)
    await asyncio.to_thread(_write_record, core, record, False)
    still_running = await asyncio.to_thread(_stop_all, apps, record, core)
    moved = await asyncio.to_thread(_move_kept_pods, core, node)
    logger.info("training mode on %s: %d models unloaded, %d workloads stopped, moved %s",
                node, len(models), len(record["workloads"]), moved)
    problems = refused + [f"still running: {w}" for w in still_running]
    if problems:
        raise TrainingModeError(
            "training mode is on, but not everything stopped (turn it off to restore what did): "
            + "; ".join(problems)
        )
    return {**record, "moved": moved}


async def turn_off() -> Dict[str, Any]:
    core, apps = _apis()
    record = await asyncio.to_thread(_read_record, core)
    if record is None:
        raise TrainingModeError("training mode is not on")

    def restore() -> Dict[str, List[str]]:
        restored, left, back = [], [], []
        for w in record["workloads"]:
            now = _current_replicas(apps, w)
            label = f"{w['kind']} {w['namespace']}/{w['name']}"
            if now is None:
                left.append(f"{label}: no longer exists")
            elif now != 0:
                left.append(f"{label}: has {now} replicas, not restored to {w['replicas']}")
            else:
                _scale(apps, w, w["replicas"])
                restored.append(label)
                back.append(w)
        _taint(core, record["node"], False)
        return {"restored": restored, "left": left, "back": back}

    result = await asyncio.to_thread(restore)
    await asyncio.to_thread(core.delete_namespaced_config_map, RECORD_NAME, RECORD_NAMESPACE)
    # The loads run on after this answers; get_llm_model_status follows each.
    asyncio.create_task(_load_one_after_another(apps, result["back"], record["models"]))
    report = {"node": record["node"], "started": record["started"], "ended": _now(),
              "restored": result["restored"], "not_restored": result["left"],
              "models_loading": record["models"]}
    logger.info("training mode off: %s", report)
    return report
