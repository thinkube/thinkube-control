#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The platform credentials reach an application as Secrets, never as manifest text.

Runs without a cluster: the module is pure, and the templates render from the
repository's templates/k8s with fake values.
"""

import sys
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from manifest_plan import kustomization_resources  # noqa: E402
from platform_credentials import (  # noqa: E402
    BUILD_NAMESPACE,
    PlatformValues,
    RETIRED_MANIFESTS,
    platform_secrets,
)

VALUES = PlatformValues(
    admin_username="tkadmin",
    admin_password="ADMIN-PW",
    mlflow_keycloak_token_url="https://auth/token",
    mlflow_keycloak_client_id="mlflow",
    mlflow_client_secret="MLFLOW-CLIENT-SECRET",
    mlflow_username="mlflow-user",
    mlflow_password="MLFLOW-PW",
    seaweedfs_endpoint="http://seaweedfs:8333",
    seaweedfs_access_key="S3-ACCESS",
    seaweedfs_secret_key="S3-SECRET",
)
CREDENTIALS = ["ADMIN-PW", "MLFLOW-CLIENT-SECRET", "MLFLOW-PW", "S3-ACCESS", "S3-SECRET"]


def names(secrets):
    return {(s["metadata"]["namespace"], s["metadata"]["name"]) for s in secrets}


def test_every_app_gets_experiments_storage_and_build_credentials():
    got = names(platform_secrets("notes", "notes", VALUES, has_database=False, has_workflows=False))
    assert got == {
        ("notes", "notes-mlflow-credentials"),
        ("notes", "notes-storage-credentials"),
        (BUILD_NAMESPACE, "notes-build-credentials"),
    }


def test_a_database_and_workflows_add_their_secrets():
    got = names(platform_secrets("wf-check", "wf-check", VALUES, has_database=True, has_workflows=True))
    assert ("wf-check", "wf-check-db-credentials") in got
    assert ("wf-check", "argo-artifacts-s3") in got


def test_the_database_secret_keeps_the_keys_the_deployments_read():
    [db] = [s for s in platform_secrets("my-app", "my-app", VALUES, has_database=True, has_workflows=False)
            if s["metadata"]["name"] == "my-app-db-credentials"]
    assert set(db["stringData"]) == {"url", "username", "password", "database", "host", "port"}
    assert db["stringData"]["database"] == "my_app"
    assert db["stringData"]["password"] == "ADMIN-PW"


def test_argocd_neither_tracks_nor_reports_the_secrets():
    for s in platform_secrets("notes", "notes", VALUES, has_database=True, has_workflows=True):
        assert s["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "thinkube-control"
        assert s["metadata"]["annotations"]["argocd.argoproj.io/compare-options"] == "IgnoreExtraneous"


def test_the_kustomization_lists_no_credential_file():
    resources = kustomization_resources(is_knative=False, needs_storage=True, has_workflows=True)
    assert not set(RETIRED_MANIFESTS) & set(resources)


def render(template, **extra):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(REPO / "templates/k8s")),
        undefined=jinja2.StrictUndefined, lstrip_blocks=True, trim_blocks=True,
    )
    env.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    env.filters["to_json"] = lambda x: __import__("json").dumps(x)
    spec = {"spec": {
        "containers": [{"name": "backend", "build": "./backend", "port": 8000,
                        "test": {"enabled": True, "image": "python-base:3.12-slim", "command": "./run_tests.sh"}}],
        "services": ["database", "workflows"],
        "deployment": {"type": "app", "replicas": 1},
    }}
    variables = dict(
        project_name="wf-check", k8s_namespace="wf-check", domain_name="thinkube.com",
        container_registry="registry.thinkube.com", admin_username="tkadmin",
        thinkube_spec=spec, manifest_params={}, seaweedfs_endpoint="http://seaweedfs:8333",
        deployment_env_from=[], system_username="thinkube", master_node_name="tkamd1",
        gitea_org="thinkube-deployments", build_architectures=["amd64", "arm64"],
    )
    variables.update(extra)
    return env.get_template(template).render(**variables)


def test_the_templates_render_without_any_credential():
    for template in ("build-workflow.j2", "deployment-separate.j2", "workflows.j2"):
        text = render(template)
        for credential in CREDENTIALS:
            assert credential not in text, (template, credential)


def test_the_build_clones_with_the_build_credentials():
    text = render("build-workflow.j2")
    assert "${GIT_USERNAME}:${GIT_PASSWORD}@git.thinkube.com" in text
    assert "wf-check-build-credentials" in text
