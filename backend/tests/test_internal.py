# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Control's reads for its extensions answer inside the cluster, with no sign-in."""

from app.models.services import Service


def add(test_db, namespace, enabled):
    s = Service(name=namespace, type="component", namespace=namespace, resource_name=f"{namespace}-x",
                service_metadata={})
    s.is_enabled = enabled
    test_db.add(s)
    test_db.commit()


def test_a_switched_off_service_is_not_enabled(client, test_db):
    add(test_db, "vllm", False)
    assert client.get("/internal/services/enabled", params={"namespace": "vllm"}).json() == {
        "namespace": "vllm", "enabled": False}


def test_a_switched_on_service_is_enabled(client, test_db):
    add(test_db, "ollama", True)
    assert client.get("/internal/services/enabled", params={"namespace": "ollama"}).json()["enabled"] is True


def test_a_namespace_without_a_service_is_enabled(client):
    assert client.get("/internal/services/enabled", params={"namespace": "nothing"}).json()["enabled"] is True
