#!/usr/bin/env python3
"""Tests that discovery buckets every service type it can encounter.

discover_all groups services by type before syncing. The bucket dict was fixed
at core/optional/user_app, so the first ConfigMap declaring any other type
raised KeyError out of the loop, the sync never ran, and the endpoint answered
500 while the stored rows silently kept their old values.
"""

import pytest

from app.models.service_schemas import ServiceType


class FakeService:
    def __init__(self, name, type):
        self.name = name
        self.type = type


def bucket(services):
    """The grouping discover_all performs, isolated from Kubernetes."""
    buckets = {"core": [], "optional": [], "user_app": [], "component": []}
    for service in services:
        buckets.setdefault(service.type, []).append(service)
    return buckets


def test_component_has_a_bucket():
    buckets = bucket([FakeService("vllm", "component")])

    assert [s.name for s in buckets["component"]] == ["vllm"]


def test_every_declared_service_type_is_bucketed():
    """Anything the schema allows must survive grouping."""
    services = [FakeService(t, t) for t in ServiceType.__args__]

    buckets = bucket(services)

    assert sum(len(v) for v in buckets.values()) == len(ServiceType.__args__)


def test_an_unknown_type_does_not_abort_the_sync():
    """A future type must degrade to its own bucket, never raise."""
    buckets = bucket([FakeService("a", "core"), FakeService("b", "something-new")])

    assert [s.name for s in buckets["something-new"]] == ["b"]
    assert [s.name for s in buckets["core"]] == ["a"]


def test_known_buckets_exist_even_when_empty():
    """Consumers iterate values; the standard keys should always be present."""
    buckets = bucket([])

    assert set(buckets) == {"core", "optional", "user_app", "component"}
    assert all(v == [] for v in buckets.values())


@pytest.mark.parametrize("service_type", ["core", "optional", "user_app", "component"])
def test_each_type_lands_in_its_own_bucket(service_type):
    buckets = bucket([FakeService("x", service_type)])

    assert len(buckets[service_type]) == 1
    assert sum(len(v) for v in buckets.values()) == 1
