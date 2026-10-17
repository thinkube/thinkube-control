# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Training mode stops what it finds outside the kept namespaces, records it,
and restores exactly that record."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from app.services import training_mode as tm


def workload(ns, name, replicas):
    return NS(metadata=NS(namespace=ns, name=name), spec=NS(replicas=replicas))


def apps_with(deployments, statefulsets=()):
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value.items = list(deployments)
    apps.list_stateful_set_for_all_namespaces.return_value.items = list(statefulsets)
    return apps


def test_only_running_workloads_outside_the_kept_namespaces_are_found():
    apps = apps_with(
        [workload("dentalpin", "dentalpin-backend", 1), workload("thinkube-control", "backend", 1),
         workload("ollama", "ollama", 0)],
        [workload("gitea", "gitea-db", 2), workload("seaweedfs", "seaweedfs-volume", 1)],
    )

    assert tm._scalable(apps) == [
        {"namespace": "dentalpin", "kind": "deployment", "name": "dentalpin-backend", "replicas": 1},
        {"namespace": "gitea", "kind": "statefulset", "name": "gitea-db", "replicas": 2},
    ]


def test_a_workload_a_controller_brings_back_is_stopped_again_and_recorded(monkeypatch):
    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    rounds = iter([
        [{"namespace": "argocd", "kind": "deployment", "name": "repo", "replicas": 1},
         {"namespace": "kn", "kind": "deployment", "name": "app-00002", "replicas": 1}],
        [],
    ])
    monkeypatch.setattr(tm, "_scalable", lambda apps: next(rounds))
    monkeypatch.setattr(tm, "_write_record", lambda core, record, create: None)
    apps = MagicMock()
    record = {"workloads": [{"namespace": "argocd", "kind": "deployment", "name": "repo", "replicas": 1}]}

    left = tm._stop_all(apps, record, MagicMock())

    assert left == []
    assert [w["name"] for w in record["workloads"]] == ["repo", "app-00002"]
    scaled = [c.args for c in apps.patch_namespaced_deployment.call_args_list]
    assert scaled.count(("repo", "argocd", {"spec": {"replicas": 0}})) == 2


def test_what_still_runs_after_the_last_round_is_named(monkeypatch):
    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    stubborn = [{"namespace": "x", "kind": "statefulset", "name": "db", "replicas": 1}]
    monkeypatch.setattr(tm, "_scalable", lambda apps: list(stubborn))
    monkeypatch.setattr(tm, "_write_record", lambda core, record, create: None)

    left = tm._stop_all(MagicMock(), {"workloads": list(stubborn)}, MagicMock())

    assert left == ["statefulset x/db"]


def test_only_pods_of_kept_deployments_leave_the_training_node():
    def pod(ns, name, owner):
        return NS(metadata=NS(namespace=ns, name=name, owner_references=[NS(kind=owner)]))

    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = [
        pod("scoring", "scoring-1", "ReplicaSet"),
        pod("jupyterhub", "proxy-1", "ReplicaSet"),
        pod("seaweedfs", "volume-0", "StatefulSet"),
        pod("dentalpin", "backend-1", "ReplicaSet"),
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

    async def no_loads(models):
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
