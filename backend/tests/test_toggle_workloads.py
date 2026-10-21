#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests that turning a service off and on scales the workloads it declares.

A service is rarely named after its workloads (todo runs todo-backend), may
have several (thinkube-flow-poc has a backend and a frontend), may run as a
StatefulSet (opensearch) or under an operator that owns its StatefulSet
(ClickHouse). Each must be turned off and back on to the replicas it had.
"""

from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from app.models.services import Service
from app.services import k8s_manager as km
from app.services.k8s_manager import K8sServiceManager, REPLICAS_BEFORE_DISABLE


def workload(replicas, annotations=None, owner=None):
    w = MagicMock()
    w.spec.replicas = replicas
    w.metadata.annotations = annotations
    if owner:
        ref = MagicMock()
        ref.controller = True
        ref.kind, ref.name = owner
        w.metadata.owner_references = [ref]
    else:
        w.metadata.owner_references = None
    return w


@pytest.fixture
def manager():
    m = K8sServiceManager.__new__(K8sServiceManager)
    m.apps_v1 = MagicMock()
    m.cleanup_stale_pods = MagicMock()
    return m


def test_every_declared_workload_is_listed():
    flow = Service(
        name="thinkube-flow-poc", type="optional", resource_type="deployment",
        resource_name="thinkube-flow-poc-backend",
        service_metadata={"resources": [
            {"resource_type": "deployment", "resource_name": "thinkube-flow-poc-backend"},
            {"resource_type": "deployment", "resource_name": "thinkube-flow-poc-frontend"},
        ]},
    )

    assert K8sServiceManager._workloads(flow) == [
        ("deployment", "thinkube-flow-poc-backend"),
        ("deployment", "thinkube-flow-poc-frontend"),
    ]


def test_the_recorded_workload_is_used_when_no_list_is_kept():
    search = Service(name="opensearch", type="optional", resource_type="statefulset",
                     resource_name="opensearch-cluster-master", service_metadata={})

    assert K8sServiceManager._workloads(search) == [("statefulset", "opensearch-cluster-master")]


def test_a_workload_that_cannot_be_scaled_is_refused_by_name():
    s = Service(name="agent", type="optional", resource_type="daemonset",
                resource_name="agent", service_metadata={})

    with pytest.raises(ValueError, match="daemonset/agent"):
        K8sServiceManager._workloads(s)


def test_turning_a_statefulset_off_records_its_replicas(manager):
    manager.apps_v1.read_namespaced_stateful_set.return_value = workload(1)

    ok, error = manager.scale_workload("opensearch", "statefulset", "opensearch-cluster-master", on=False)

    assert (ok, error) == (True, None)
    _, _, body = manager.apps_v1.patch_namespaced_stateful_set.call_args.args
    assert body == {"metadata": {"annotations": {REPLICAS_BEFORE_DISABLE: "1"}}, "spec": {"replicas": 0}}


def test_turning_on_restores_the_recorded_replicas(manager):
    manager.apps_v1.read_namespaced_deployment.return_value = workload(0, {REPLICAS_BEFORE_DISABLE: "2"})

    ok, _ = manager.scale_workload("todo", "deployment", "todo-backend", on=True)

    assert ok
    _, _, body = manager.apps_v1.patch_namespaced_deployment.call_args.args
    assert body["spec"] == {"replicas": 2}


def test_turning_on_without_a_recorded_count_is_refused(manager):
    manager.apps_v1.read_namespaced_deployment.return_value = workload(0, {})

    ok, error = manager.scale_workload("todo", "deployment", "todo-backend", on=True)

    assert ok is False
    assert REPLICAS_BEFORE_DISABLE in error
    manager.apps_v1.patch_namespaced_deployment.assert_not_called()


def test_a_missing_workload_is_named(manager):
    manager.apps_v1.read_namespaced_stateful_set.side_effect = ApiException(status=404)

    ok, error = manager.scale_workload("opensearch", "statefulset", "gone", on=False)

    assert ok is False
    assert error == "statefulset gone not found in namespace opensearch"


def test_clickhouse_is_stopped_through_its_installation(manager, monkeypatch):
    manager.apps_v1.read_namespaced_stateful_set.return_value = workload(
        1, owner=("ClickHouseInstallation", "clickhouse"))
    custom = MagicMock()
    monkeypatch.setattr(km.client, "CustomObjectsApi", lambda: custom)

    ok, _ = manager.scale_workload("clickhouse", "statefulset", "chi-clickhouse-clickhouse-0-0", on=False)

    assert ok
    custom.patch_namespaced_custom_object.assert_called_once_with(
        "clickhouse.altinity.com", "v1", "clickhouse", "clickhouseinstallations", "clickhouse",
        {"spec": {"stop": "yes"}},
    )
    manager.apps_v1.patch_namespaced_stateful_set.assert_not_called()


def test_a_workload_another_controller_owns_is_refused(manager):
    manager.apps_v1.read_namespaced_deployment.return_value = workload(0, owner=("Revision", "app-00001"))

    ok, error = manager.scale_workload("app", "deployment", "app-00001-deployment", on=True)

    assert ok is False
    assert "managed by Revision app-00001" in error
