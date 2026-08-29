#!/usr/bin/env python3
"""Tests for pinning architecture-limited optional components to capable nodes.

cvat publishes single-arch linux/amd64 images, and the cluster has arm nodes,
so it has to declare which architectures it can run on. That declaration both
gates installability and produces the node selector its playbook applies.
"""

from unittest.mock import MagicMock

import pytest

from app.services.optional_components import OptionalComponentService

ARCH_LABEL = OptionalComponentService.ARCH_LABEL


@pytest.fixture
def service(monkeypatch):
    s = OptionalComponentService.__new__(OptionalComponentService)
    return s


def with_nodes(service, monkeypatch, arches):
    monkeypatch.setattr(service, "_cluster_architectures", lambda: set(arches))


def test_amd64_component_is_available_on_a_mixed_cluster(service, monkeypatch):
    with_nodes(service, monkeypatch, {"amd64", "arm64"})

    met, missing = service._architecture_status({"architectures": ["amd64"]})

    assert met is True
    assert missing == []


def test_amd64_component_is_unavailable_on_an_arm_only_cluster(service, monkeypatch):
    with_nodes(service, monkeypatch, {"arm64"})

    met, missing = service._architecture_status({"architectures": ["amd64"]})

    assert met is False
    assert missing == ["a amd64 node"]


def test_a_component_without_a_declaration_runs_anywhere(service, monkeypatch):
    with_nodes(service, monkeypatch, {"arm64"})

    assert service._architecture_status({})[0] is True
    assert service._architecture_status({"architectures": []})[0] is True


def test_an_unreadable_cluster_does_not_block_installation(service, monkeypatch):
    """A failed node read must not make every pinned component look impossible."""
    with_nodes(service, monkeypatch, set())

    assert service._architecture_status({"architectures": ["amd64"]})[0] is True


def test_node_selector_pins_a_single_architecture(monkeypatch):
    monkeypatch.setattr(
        "app.services.optional_components.get_components_catalog",
        lambda: {"cvat": {"architectures": ["amd64"]}},
    )
    s = OptionalComponentService.__new__(OptionalComponentService)

    assert s.node_selector("cvat") == {ARCH_LABEL: "amd64"}


@pytest.mark.parametrize(
    "catalog",
    [
        {"qdrant": {"architectures": []}},
        {"qdrant": {}},
        {"qdrant": {"architectures": ["amd64", "arm64"]}},
    ],
)
def test_node_selector_is_empty_when_nothing_to_pin(catalog, monkeypatch):
    """No declaration, or several architectures, means no restriction."""
    monkeypatch.setattr(
        "app.services.optional_components.get_components_catalog", lambda: catalog
    )
    s = OptionalComponentService.__new__(OptionalComponentService)

    assert s.node_selector("qdrant") == {}


def test_node_selector_of_an_unknown_component_is_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.optional_components.get_components_catalog", lambda: {}
    )
    s = OptionalComponentService.__new__(OptionalComponentService)

    assert s.node_selector("nope") == {}


def test_cluster_architectures_reads_the_node_label(monkeypatch):
    node = MagicMock()
    node.metadata.labels = {ARCH_LABEL: "amd64"}
    other = MagicMock()
    other.metadata.labels = {ARCH_LABEL: "arm64"}
    api = MagicMock()
    api.list_node.return_value.items = [node, other]
    monkeypatch.setattr("kubernetes.client.CoreV1Api", lambda: api)

    s = OptionalComponentService.__new__(OptionalComponentService)

    assert s._cluster_architectures() == {"amd64", "arm64"}
