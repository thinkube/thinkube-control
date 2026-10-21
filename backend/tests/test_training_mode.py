# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Training mode stops what it finds on the training node outside the kept
namespaces, records it, and restores exactly that record."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from app.services import training_mode as tm


def workload(ns, name, replicas):
    return NS(metadata=NS(namespace=ns, name=name), spec=NS(replicas=replicas))


def owned(ns, name, owner_kind, owner_name):
    return NS(metadata=NS(namespace=ns, name=name, owner_references=[NS(kind=owner_kind, name=owner_name)]))


def pod(ns, name, node, owner_kind, owner_name, phase="Running"):
    return NS(metadata=NS(namespace=ns, name=name, owner_references=[NS(kind=owner_kind, name=owner_name)]),
              spec=NS(node_name=node), status=NS(phase=phase))


def cluster(deployments, statefulsets, replica_sets, pods):
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value.items = list(deployments)
    apps.list_stateful_set_for_all_namespaces.return_value.items = list(statefulsets)
    apps.list_replica_set_for_all_namespaces.return_value.items = list(replica_sets)
    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = list(pods)
    return core, apps


def test_only_workloads_with_a_pod_on_the_training_node_are_found():
    core, apps = cluster(
        [workload("dentalpin", "dentalpin-backend", 1), workload("dentalpin", "dentalpin-frontend", 1),
         workload("thinkube-control", "backend", 1), workload("ollama", "ollama", 0),
         workload("web", "spread", 3)],
        [workload("gitea", "gitea-db", 2), workload("seaweedfs", "seaweedfs-volume", 1)],
        [owned("dentalpin", "dentalpin-backend-7f", "Deployment", "dentalpin-backend"),
         owned("dentalpin", "dentalpin-frontend-5c", "Deployment", "dentalpin-frontend"),
         owned("thinkube-control", "backend-1a", "Deployment", "backend"),
         owned("web", "spread-9d", "Deployment", "spread")],
        [pod("dentalpin", "dentalpin-backend-7f-x", "tkspark", "ReplicaSet", "dentalpin-backend-7f"),
         pod("dentalpin", "dentalpin-frontend-5c-x", "tkamd1", "ReplicaSet", "dentalpin-frontend-5c"),
         pod("thinkube-control", "backend-1a-x", "tkspark", "ReplicaSet", "backend-1a"),
         pod("gitea", "gitea-db-0", "tkspark", "StatefulSet", "gitea-db"),
         pod("seaweedfs", "seaweedfs-volume-0", "tkspark", "StatefulSet", "seaweedfs-volume"),
         pod("web", "spread-9d-a", "tkamd1", "ReplicaSet", "spread-9d"),
         pod("web", "spread-9d-b", "tkspark", "ReplicaSet", "spread-9d"),
         pod("web", "spread-9d-c", "tkamd2", "ReplicaSet", "spread-9d")],
    )

    assert tm._scalable(core, apps, "tkspark") == [
        {"namespace": "dentalpin", "kind": "deployment", "name": "dentalpin-backend", "replicas": 1,
         "nodes": ["tkspark"]},
        {"namespace": "web", "kind": "deployment", "name": "spread", "replicas": 3,
         "nodes": ["tkamd1", "tkamd2", "tkspark"]},
        {"namespace": "gitea", "kind": "statefulset", "name": "gitea-db", "replicas": 2, "nodes": ["tkspark"]},
    ]


def test_a_workload_whose_pods_are_on_other_nodes_or_finished_is_left_running():
    core, apps = cluster(
        [workload("dentalpin", "dentalpin-backend", 1)],
        [workload("batch", "runner", 1)],
        [owned("dentalpin", "dentalpin-backend-7f", "Deployment", "dentalpin-backend")],
        [pod("dentalpin", "dentalpin-backend-7f-x", "tkamd2", "ReplicaSet", "dentalpin-backend-7f"),
         pod("batch", "runner-0", "tkspark", "StatefulSet", "runner", phase="Succeeded"),
         pod("batch", "runner-1", None, "StatefulSet", "runner", phase="Pending")],
    )

    assert tm._scalable(core, apps, "tkspark") == []


def test_only_the_models_on_the_training_node_are_unloaded(monkeypatch):
    import asyncio
    import sys
    import types

    status = NS(nodes=[NS(name="tkspark", allocations=[NS(model_id="a")]),
                       NS(name="tkamd2", allocations=[NS(model_id="b")])])

    async def get_status():
        return status

    fake = types.ModuleType("app.services.llm_gpu_tracker")
    fake.llm_gpu_tracker = NS(get_status=get_status)
    monkeypatch.setitem(sys.modules, "app.services.llm_gpu_tracker", fake)

    assert asyncio.run(tm._loaded_models("tkspark")) == [{"model_id": "a", "node": "tkspark"}]


def test_the_training_node_gets_the_taint_and_the_label_and_loses_both():
    core = MagicMock()
    core.read_node.return_value = NS(spec=NS(taints=None))
    tm._taint(core, "tkspark", True)
    body = core.patch_node.call_args.args[1]
    assert body["metadata"]["labels"] == {tm.TRAINING_LABEL: "true"}
    assert body["spec"]["taints"] == [{"key": tm.TRAINING_TAINT, "value": "true", "effect": "NoSchedule",
                                       "time_added": None}]

    core.read_node.return_value = NS(spec=NS(taints=[MagicMock(key=tm.TRAINING_TAINT)]))
    tm._taint(core, "tkspark", False)
    body = core.patch_node.call_args.args[1]
    assert body == {"metadata": {"labels": {tm.TRAINING_LABEL: None}}, "spec": {"taints": None}}


def test_a_workload_a_controller_brings_back_is_stopped_again_and_recorded(monkeypatch):
    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    rounds = iter([
        [{"namespace": "argocd", "kind": "deployment", "name": "repo", "replicas": 1},
         {"namespace": "kn", "kind": "deployment", "name": "app-00002", "replicas": 1}],
        [],
    ])
    monkeypatch.setattr(tm, "_scalable", lambda core, apps, node: next(rounds))
    monkeypatch.setattr(tm, "_write_record", lambda core, record, create: None)
    apps = MagicMock()
    record = {"node": "tkspark",
              "workloads": [{"namespace": "argocd", "kind": "deployment", "name": "repo", "replicas": 1}]}

    left = tm._stop_all(apps, record, MagicMock())

    assert left == []
    assert [w["name"] for w in record["workloads"]] == ["repo", "app-00002"]
    scaled = [c.args for c in apps.patch_namespaced_deployment.call_args_list]
    assert scaled.count(("repo", "argocd", {"spec": {"replicas": 0}})) == 2


def test_what_still_runs_after_the_last_round_is_named(monkeypatch):
    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    stubborn = [{"namespace": "x", "kind": "statefulset", "name": "db", "replicas": 1}]
    monkeypatch.setattr(tm, "_scalable", lambda core, apps, node: list(stubborn))
    monkeypatch.setattr(tm, "_write_record", lambda core, record, create: None)

    left = tm._stop_all(MagicMock(), {"node": "tkspark", "workloads": list(stubborn)}, MagicMock())

    assert left == ["statefulset x/db"]


def test_only_pods_of_kept_deployments_leave_the_training_node():
    def kept(ns, name, owner):
        return NS(metadata=NS(namespace=ns, name=name, owner_references=[NS(kind=owner)]))

    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = [
        kept("scoring", "scoring-1", "ReplicaSet"),
        kept("jupyterhub", "proxy-1", "ReplicaSet"),
        kept("seaweedfs", "volume-0", "StatefulSet"),
        kept("dentalpin", "backend-1", "ReplicaSet"),
    ]

    assert tm._move_kept_pods(core, "tkspark") == ["scoring/scoring-1"]


def test_turning_off_restores_the_record_and_leaves_what_changed(monkeypatch):
    import asyncio

    record = {"node": "tkspark", "started": "t0", "models": [], "workloads": [
        {"namespace": "dentalpin", "kind": "deployment", "name": "backend", "replicas": 2},
        {"namespace": "gitea", "kind": "statefulset", "name": "gitea", "replicas": 1},
        {"namespace": "gone", "kind": "deployment", "name": "app", "replicas": 1},
    ]}
    core, apps = MagicMock(), MagicMock()
    monkeypatch.setattr(tm, "_apis", lambda: (core, apps))
    monkeypatch.setattr(tm, "_read_record", lambda c: record)
    monkeypatch.setattr(tm, "_taint", lambda c, node, on: None)
    now = {"backend": 0, "gitea": 3, "app": None}
    monkeypatch.setattr(tm, "_current_replicas", lambda a, w: now[w["name"]])

    async def no_loads(apps, restored, models):
        return None

    monkeypatch.setattr(tm, "_load_one_after_another", no_loads)
    report = asyncio.run(tm.turn_off())

    apps.patch_namespaced_deployment.assert_called_once_with("backend", "dentalpin", {"spec": {"replicas": 2}})
    assert report["restored"] == ["deployment dentalpin/backend"]
    assert report["not_restored"] == [
        "statefulset gitea/gitea: has 3 replicas, not restored to 1",
        "deployment gone/app: no longer exists",
    ]
    core.delete_namespaced_config_map.assert_called_once_with(tm.RECORD_NAME, tm.RECORD_NAMESPACE)


def test_a_workload_deleted_since_it_was_listed_leaves_the_record(monkeypatch):
    from kubernetes.client.rest import ApiException

    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    monkeypatch.setattr(tm, "_scalable", lambda core, apps, node: [])
    monkeypatch.setattr(tm, "_write_record", lambda core, record, create: None)
    apps = MagicMock()

    def patch(name, ns, body):
        if name == "gone":
            raise ApiException(status=404)

    apps.patch_namespaced_deployment.side_effect = patch
    record = {"node": "tkspark",
              "workloads": [{"namespace": "vllm", "kind": "deployment", "name": "gone", "replicas": 1},
                            {"namespace": "dentalpin", "kind": "deployment", "name": "backend", "replicas": 1}]}

    assert tm._stop_all(apps, record, MagicMock()) == []
    assert [w["name"] for w in record["workloads"]] == ["backend"]
