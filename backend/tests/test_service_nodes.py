#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the nodes a service's card shows: the nodes its running pods are on."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.k8s_manager import K8sServiceManager


def pod(namespace, node):
    return SimpleNamespace(
        metadata=SimpleNamespace(namespace=namespace),
        spec=SimpleNamespace(node_name=node),
    )


def manager(pods):
    m = K8sServiceManager.__new__(K8sServiceManager)
    m.core_v1 = MagicMock()
    m.core_v1.list_pod_for_all_namespaces.return_value = SimpleNamespace(items=pods)
    return m


def test_nodes_are_grouped_by_namespace_sorted_and_unique():
    m = manager([
        pod("juicefs", "tkamd1"),
        pod("envoy-gateway-system", "tkamd2"),
        pod("juicefs", "tkamd1"),
        pod("vllm", "tkspark"),
        pod("envoy-gateway-system", "tkamd1"),
    ])
    assert m.get_nodes_by_namespace() == {
        "juicefs": ["tkamd1"],
        "envoy-gateway-system": ["tkamd1", "tkamd2"],
        "vllm": ["tkspark"],
    }


def test_only_running_pods_are_asked_for():
    m = manager([])
    m.get_nodes_by_namespace()
    m.core_v1.list_pod_for_all_namespaces.assert_called_once_with(field_selector="status.phase=Running")


def test_a_pod_not_yet_on_a_node_is_left_out():
    m = manager([pod("vllm", None)])
    assert m.get_nodes_by_namespace() == {}
