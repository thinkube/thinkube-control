#!/usr/bin/env python3
"""The Knative Service the generator renders is one Knative Serving accepts.

Knative Serving decodes a Service strictly and refuses a container field it
does not support, so every field rendered into the container must be one it
knows. resizePolicy is not.
"""

import json
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
DEMO = REPO.parent.parent / "templates/tkt-knative-demo/thinkube.yaml"


def render(spec):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    env.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    env.filters["to_json"] = lambda x: json.dumps(x)
    return env.get_template("knative-service.j2").render(
        project_name="knative-demo",
        k8s_namespace="knative-demo",
        domain_name="thinkube.com",
        container_registry="registry.thinkube.com",
        admin_username="tkadmin",
        admin_password="x",
        thinkube_spec=spec,
        manifest_params={},
        mlflow_keycloak_token_url="",
        mlflow_keycloak_client_id="",
        mlflow_client_secret="",
        mlflow_username="",
        mlflow_password="",
        seaweedfs_password="",
        seaweedfs_access_key="",
        seaweedfs_endpoint="",
    )


SPEC = {"spec": {
    "deployment": {"type": "knative"},
    "containers": [{"name": "demo", "build": ".", "port": 8080, "size": "small", "health": "/health"}],
}}


def containers(rendered):
    [service] = [d for d in yaml.safe_load_all(rendered) if d]
    return service["spec"]["template"]["spec"]["containers"]


def test_no_container_carries_resize_policy():
    for container in containers(render(SPEC)):
        assert "resizePolicy" not in container


def test_the_platform_demo_template_renders_without_resize_policy():
    spec = yaml.safe_load(DEMO.read_text())
    for container in containers(render(spec)):
        assert "resizePolicy" not in container
        assert container["readinessProbe"]["httpGet"]["path"] == "/health"
