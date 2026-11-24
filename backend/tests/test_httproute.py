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


def render(spec, extension=False):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
    )
    rendered = env.get_template("httproute.j2").render(
        project_name="dentalpin",
        k8s_namespace="dentalpin",
        domain_name="thinkube.com",
        thinkube_spec=spec,
        extension=extension,
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


def test_an_extension_routes_to_services_named_after_it():
    route = render({"spec": {
        "containers": [BACKEND, FRONTEND],
        "routes": [{"path": "/api", "to": "backend"}, {"path": "/", "to": "frontend"}],
    }}, extension=True)
    assert [r["backendRefs"][0]["name"] for r in route["spec"]["rules"]] == ["dentalpin-backend", "dentalpin-frontend"]


def services(extension):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), undefined=jinja2.StrictUndefined)
    rendered = env.get_template("services-separate.j2").render(
        project_name="dentalpin", k8s_namespace="dentalpin",
        thinkube_spec={"spec": {"containers": [BACKEND, FRONTEND]}}, extension=extension,
    )
    return [d["metadata"]["name"] for d in yaml.safe_load_all(rendered) if d]


def test_an_apps_services_keep_their_container_names_and_an_extensions_carry_its_name():
    assert services(extension=False) == ["backend", "frontend"]
    assert services(extension=True) == ["dentalpin-backend", "dentalpin-frontend"]


def test_a_route_with_a_host_is_served_at_that_platform_address_by_a_route_of_its_own():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), undefined=jinja2.StrictUndefined)
    proxy = {"name": "proxy", "build": "./proxy", "port": 8080, "health": "/health"}
    rendered = env.get_template("httproute.j2").render(
        project_name="control-llm", k8s_namespace="thinkube-control", domain_name="thinkube.com",
        thinkube_spec={"spec": {"containers": [BACKEND, FRONTEND, proxy], "routes": [
            {"path": "/api", "to": "backend"}, {"path": "/", "to": "frontend"},
            {"path": "/", "to": "proxy", "host": "llm"}]}},
        extension=True,
    )
    own, llm = [d for d in yaml.safe_load_all(rendered) if d]
    assert own["spec"]["hostnames"] == ["control-llm.thinkube.com"] and len(own["spec"]["rules"]) == 2
    assert llm["metadata"]["name"] == "control-llm-llm"
    assert llm["metadata"]["labels"]["app.kubernetes.io/name"] == "control-llm"
    assert llm["spec"]["hostnames"] == ["llm.thinkube.com"]
    [rule] = llm["spec"]["rules"]
    assert rule["backendRefs"] == [{"name": "control-llm-proxy", "port": 8080}]
    assert rule["timeouts"] == {"request": "600s"}


def test_only_the_extension_that_owns_a_platform_address_may_take_it():
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from thinkube_yaml_validator import validate_route_hosts

    def config(name, kind, host):
        return {"metadata": {"name": name}, "spec": {"deployment": {"type": kind},
                                                     "routes": [{"path": "/", "to": "proxy", "host": host}]}}

    assert validate_route_hosts(config("control-llm", "extension", "llm")) == []
    assert "extension only" in validate_route_hosts(config("control-llm", "app", "llm"))[0]
    assert "not control-models's" in validate_route_hosts(config("control-models", "extension", "llm"))[0]
    assert "not a platform address" in validate_route_hosts(config("control-llm", "extension", "storage"))[0]


def test_a_platform_address_answers_in_the_cluster_at_its_own_service_name():
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from manifest_plan import PLATFORM_ADDRESSES

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), undefined=jinja2.StrictUndefined)
    proxy = {"name": "proxy", "build": "./proxy", "port": 8080, "health": "/health"}
    rendered = env.get_template("services-separate.j2").render(
        project_name="control-llm", k8s_namespace="thinkube-control", extension=True,
        platform_addresses=PLATFORM_ADDRESSES,
        thinkube_spec={"spec": {"containers": [BACKEND, proxy],
                                "routes": [{"path": "/api", "to": "backend"},
                                           {"path": "/", "to": "proxy", "host": "llm"}]}},
    )
    services = {d["metadata"]["name"]: d for d in yaml.safe_load_all(rendered) if d}
    assert set(services) == {"control-llm-backend", "control-llm-proxy", "llm-proxy"}
    assert services["llm-proxy"]["spec"]["selector"] == services["control-llm-proxy"]["spec"]["selector"]
    assert services["llm-proxy"]["spec"]["ports"][0]["port"] == 8080
