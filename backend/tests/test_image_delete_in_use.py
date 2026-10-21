# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An image record is not deleted while a running pod uses the image.

A pod may name an image tagged latest without its tag; finished pods do
not count.
"""

from types import SimpleNamespace

import app.api.harbor_images as hi

IMAGE = "registry.thinkube.com/library/alpine:latest"


def pod(ns, name, image, phase="Running", init=False):
    container = SimpleNamespace(image=image)
    spec = SimpleNamespace(containers=[] if init else [container],
                           init_containers=[container] if init else None)
    return SimpleNamespace(metadata=SimpleNamespace(namespace=ns, name=name),
                           spec=spec, status=SimpleNamespace(phase=phase))


def cluster(monkeypatch, pods):
    monkeypatch.setattr(hi.k8s_config, "load_incluster_config", lambda: None)
    api = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=pods))
    monkeypatch.setattr(hi.k8s_client, "CoreV1Api", lambda: api)


def test_running_pods_with_the_image_are_named(monkeypatch):
    cluster(monkeypatch, [
        pod("apps", "web-1", IMAGE),
        pod("apps", "web-2", "registry.thinkube.com/library/alpine"),
        pod("jobs", "init-1", IMAGE, init=True),
        pod("apps", "other", "registry.thinkube.com/library/busybox:latest"),
    ])
    assert hi.pods_using(IMAGE) == ["apps/web-1", "apps/web-2", "jobs/init-1"]


def test_finished_pods_do_not_count(monkeypatch):
    cluster(monkeypatch, [pod("jobs", "done", IMAGE, phase="Succeeded"),
                          pod("jobs", "broke", IMAGE, phase="Failed")])
    assert hi.pods_using(IMAGE) == []


def test_an_untagged_name_matches_only_latest(monkeypatch):
    cluster(monkeypatch, [pod("apps", "web", "registry.thinkube.com/library/alpine")])
    assert hi.pods_using("registry.thinkube.com/library/alpine:3.20") == []
