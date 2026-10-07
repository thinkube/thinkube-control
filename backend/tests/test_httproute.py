#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Every rule of an application's HTTPRoute allows a request 600 seconds.

Envoy's default route timeout is 15 seconds, which ends a long response, such
as a streamed LLM answer, in the middle.
"""

from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"


def render(spec):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
    )
    rendered = env.get_template("httproute.j2").render(
        project_name="dentalpin",
        k8s_namespace="dentalpin",
        domain_name="thinkube.com",
        thinkube_spec=spec,
    )
    [route] = [d for d in yaml.safe_load_all(rendered) if d]
    return route


BACKEND = {"name": "backend", "build": "./backend", "port": 8000, "health": "/health"}
FRONTEND = {"name": "frontend", "build": ".", "port": 3000, "health": "/"}


def test_every_declared_route_allows_600_seconds():
    route = render({"spec": {
        "containers": [BACKEND, FRONTEND],
        "routes": [{"path": "/api", "to": "backend"}, {"path": "/", "to": "frontend"}],
    }})
    rules = route["spec"]["rules"]
    assert [r["matches"][0]["path"]["value"] for r in rules] == ["/api", "/"]
    assert all(r["timeouts"] == {"request": "600s"} for r in rules)


def test_the_route_without_declared_routes_allows_600_seconds():
    route = render({"spec": {"containers": [FRONTEND]}})
    [rule] = route["spec"]["rules"]
    assert rule["timeouts"] == {"request": "600s"}
