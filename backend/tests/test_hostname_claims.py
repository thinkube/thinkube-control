# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A deploy is refused when another service already answers at the app's address."""

from app.services.hostname_claims import other_claim


def route(namespace, name, host, label=None):
    labels = {"app.kubernetes.io/name": label} if label else {}
    return {"metadata": {"namespace": namespace, "name": name, "labels": labels}, "spec": {"hostnames": [host]}}


ROUTES = [
    route("seaweedfs", "seaweedfs-ui-httproute", "storage.thinkube.com"),
    route("thinkube-control", "forge", "forge.thinkube.com", label="forge"),
    route("thinkube-control", "thinkube-control-httproute", "control.thinkube.com"),
    route("dentalpin", "dentalpin", "dentalpin.thinkube.com"),
]


def test_a_platform_services_address_is_taken():
    assert other_claim(ROUTES, "storage", "thinkube-control", True, "thinkube.com") == \
        "seaweedfs/seaweedfs-ui-httproute"
    assert other_claim(ROUTES, "storage", "storage", False, "thinkube.com") == "seaweedfs/seaweedfs-ui-httproute"


def test_an_apps_own_route_is_not_another_claim():
    assert other_claim(ROUTES, "dentalpin", "dentalpin", False, "thinkube.com") is None


def test_an_extensions_own_route_is_known_by_its_label():
    assert other_claim(ROUTES, "forge", "thinkube-control", True, "thinkube.com") is None
    assert other_claim(ROUTES, "control", "thinkube-control", True, "thinkube.com") == \
        "thinkube-control/thinkube-control-httproute"


def test_a_free_address_has_no_claim():
    assert other_claim(ROUTES, "control-storage", "thinkube-control", True, "thinkube.com") is None
