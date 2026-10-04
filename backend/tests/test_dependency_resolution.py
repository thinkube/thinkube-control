#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A thinkube.yaml dependency resolves to the running service its type names."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from dependency_resolution import (  # noqa: E402
    KnativeView,
    ServiceView,
    knative_url,
    knative_view,
    service_url,
    service_view,
)

# Services of the reference cluster, as the Kubernetes API lists them.
SERVICES = [
    ServiceView("kubernetes", "default", 443),
    ServiceView("backend", "thinkube-control", 8000),
    ServiceView("llm-proxy", "thinkube-control", 8080),
    ServiceView("seaweedfs-filer", "seaweedfs", 8333),
    ServiceView("seaweedfs-gateway", "seaweedfs-gateway", 8080),
]


def test_service_matched_by_name_in_any_namespace():
    assert service_url(SERVICES, "llm-proxy") == "http://llm-proxy.thinkube-control.svc.cluster.local:8080"


def test_service_matching_name_and_namespace_is_preferred():
    assert service_url(SERVICES, "seaweedfs-gateway") == (
        "http://seaweedfs-gateway.seaweedfs-gateway.svc.cluster.local:8080"
    )


def test_services_in_system_namespaces_are_not_candidates():
    assert service_url(SERVICES, "kubernetes") is None


def test_no_match_is_none():
    assert service_url(SERVICES, "texplitter") is None


def test_service_without_port_is_refused():
    with pytest.raises(RuntimeError, match="exposes no port"):
        service_url([ServiceView("llm-proxy", "thinkube-control", None)], "llm-proxy")


def test_knative_service_matched_by_namespace():
    services = [KnativeView("app", "texplitter", "http://app.texplitter.svc.cluster.local")]
    assert knative_url(services, "texplitter") == "http://app.texplitter.svc.cluster.local"


def test_knative_service_without_address_is_refused():
    with pytest.raises(RuntimeError, match="not ready"):
        knative_url([KnativeView("texplitter", "texplitter", None)], "texplitter")


def test_views_from_api_objects():
    item = {"metadata": {"name": "a", "namespace": "b"}, "status": {"address": {"url": "http://a.b"}}}
    assert knative_view(item) == KnativeView("a", "b", "http://a.b")
    assert knative_view({"metadata": {"name": "a", "namespace": "b"}}).url is None

    svc = SimpleNamespace(
        metadata=SimpleNamespace(name="llm-proxy", namespace="thinkube-control"),
        spec=SimpleNamespace(ports=[SimpleNamespace(port=8080)]),
    )
    assert service_view(svc) == ServiceView("llm-proxy", "thinkube-control", 8080)
    svc.spec.ports = None
    assert service_view(svc).first_port is None
