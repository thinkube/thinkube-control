#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The workflows service for an extension, which runs in Thinkube Control's namespace.

These render the real templates in templates/k8s/ with the variables the
generator passes for an extension, so what is asserted here is what lands in
the extension's k8s/.
"""

import json
import sys
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

import thinkube_yaml_validator  # noqa: E402
from manifest_plan import (  # noqa: E402
    EXTENSION_NAMESPACE,
    artifact_repository_name,
    kustomization_resources,
)
from platform_credentials import ARTIFACT_SECRET_NAME, artifact_secret_name  # noqa: E402

EXTENSION = "datasets"


def env():
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    environment.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    environment.filters["to_json"] = lambda x: json.dumps(x)
    return environment


def spec(services=None):
    return {
        "apiVersion": "thinkube.io/v1",
        "kind": "ThinkubeDeployment",
        "metadata": {"name": EXTENSION},
        "spec": {
            "deployment": {"type": "extension", "replicas": 1},
            "extension": {
                "title": "Datasets",
                "platform": "0.1",
                "ui": {"module": "/extension/index.js"},
                "nav": [{"label": "Datasets", "path": "/"}],
            },
            "containers": [
                {"name": "backend", "build": "./backend", "port": 8000, "health": "/health"},
                {"name": "frontend", "build": "./frontend", "port": 80, "health": "/health"},
            ],
            **({"services": services} if services is not None else {}),
        },
    }


def template_vars(config):
    return {
        "project_name": EXTENSION,
        "k8s_namespace": EXTENSION_NAMESPACE,
        "domain_name": "thinkube.com",
        "container_registry": "registry.thinkube.com",
        "admin_username": "tkadmin",
        "thinkube_spec": config,
        "manifest_params": {},
        "spec_env": {},
        "seaweedfs_endpoint": "http://seaweedfs-filer.seaweedfs.svc.cluster.local:8333",
        "deployment_env_from": [],
        "extension": True,
        "artifact_repository": artifact_repository_name(EXTENSION, True),
        "artifact_secret": artifact_secret_name(EXTENSION, True),
    }


def render(name, config):
    return env().get_template(name).render(**template_vars(config))


def docs_of(text):
    return [d for d in yaml.safe_load_all(text) if d]


def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


def workflows_docs():
    return docs_of(render("workflows.j2", spec(services=["database", "workflows"])))


def repository(docs):
    cm = by_kind(docs, "ConfigMap")[0]
    return cm, yaml.safe_load(cm["data"]["default-v1"])


# --- the validator -----------------------------------------------------------


def test_an_extension_may_declare_workflows():
    """Every check the deploy runs on thinkube.yaml passes."""
    config = spec(services=["database", "workflows"])
    for check in (
        thinkube_yaml_validator.validate_knative_constraints,
        thinkube_yaml_validator.validate_component_constraints,
        thinkube_yaml_validator.validate_replicas,
        thinkube_yaml_validator.validate_llm_backend,
    ):
        assert check(config) == [], check.__name__


def test_knative_still_refuses_workflows():
    config = spec(services=["workflows"])
    config["spec"]["deployment"] = {"type": "knative"}
    config["spec"]["containers"] = config["spec"]["containers"][:1]
    assert thinkube_yaml_validator.validate_knative_constraints(config)


# --- names -------------------------------------------------------------------


def test_an_extension_names_its_repository_and_credentials_after_itself():
    assert artifact_repository_name(EXTENSION, True) == "datasets-artifact-repositories"
    assert artifact_secret_name(EXTENSION, True) == "datasets-artifacts-s3"


def test_an_app_keeps_the_namespace_default_names():
    assert artifact_repository_name("wf-check", False) == "artifact-repositories"
    assert artifact_secret_name("wf-check", False) == ARTIFACT_SECRET_NAME


def test_two_extensions_never_share_a_repository_or_its_credentials():
    assert artifact_repository_name("datasets", True) != artifact_repository_name("forge", True)
    assert artifact_secret_name("datasets", True) != artifact_secret_name("forge", True)


# --- workflows.yaml ----------------------------------------------------------


def test_everything_lands_in_controls_namespace_with_the_extensions_label():
    for d in workflows_docs():
        assert d["metadata"]["namespace"] == "thinkube-control", d["kind"]
        assert d["metadata"]["labels"]["app.kubernetes.io/name"] == EXTENSION, d["kind"]


def test_the_account_role_and_binding_carry_the_extensions_name():
    docs = workflows_docs()
    assert [d["metadata"]["name"] for d in by_kind(docs, "ServiceAccount")] == ["datasets-workflows"]
    assert [d["metadata"]["name"] for d in by_kind(docs, "Role")] == ["datasets-workflows"]
    binding = by_kind(docs, "RoleBinding")[0]
    assert binding["metadata"]["name"] == "datasets-workflows"
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "datasets-workflows",
    }
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "datasets-workflows", "namespace": "thinkube-control"}
    ]


def rules():
    role = by_kind(workflows_docs(), "Role")[0]
    return {tuple(r["resources"]): r for r in role["rules"]}


def test_the_role_submits_reads_and_deletes_workflows_only():
    found = rules()
    rule = found[("workflows",)]
    assert rule["apiGroups"] == ["argoproj.io"]
    assert set(rule["verbs"]) == {"get", "list", "watch", "create", "delete"}
    for resources in found:
        assert "workflowtemplates" not in resources
        assert "cronworkflows" not in resources


def test_the_role_lets_the_executor_report_each_step():
    assert set(rules()[("workflowtaskresults",)]["verbs"]) == {"create", "patch"}


def test_the_role_reads_pods_and_logs_and_changes_neither():
    rule = rules()[("pods", "pods/log")]
    assert rule["apiGroups"] == [""]
    assert set(rule["verbs"]) == {"get", "list", "watch"}


def test_the_role_reaches_nothing_else_in_controls_namespace():
    for resources, rule in rules().items():
        assert set(resources) <= {"workflows", "workflowtaskresults", "pods", "pods/log"}
        assert "*" not in rule["verbs"]
        assert "secrets" not in resources


def test_the_repository_is_the_extensions_own_configmap():
    cm, _ = repository(workflows_docs())
    assert cm["metadata"]["name"] == "datasets-artifact-repositories"
    # Argo takes a namespace default only from a ConfigMap of this name.
    assert cm["metadata"]["name"] != "artifact-repositories"
    assert cm["metadata"]["annotations"]["workflows.argoproj.io/default-artifact-repository"] == "default-v1"


def test_the_extensions_artifacts_go_under_its_own_prefix():
    _, repo = repository(workflows_docs())
    assert repo["s3"]["bucket"] == "argo-artifacts"
    assert repo["s3"]["keyFormat"] == "datasets/{{workflow.name}}/{{pod.name}}"


def test_the_repository_names_the_extensions_own_credentials():
    _, repo = repository(workflows_docs())
    assert repo["s3"]["accessKeySecret"] == {"name": "datasets-artifacts-s3", "key": "accesskey"}
    assert repo["s3"]["secretKeySecret"] == {"name": "datasets-artifacts-s3", "key": "secretkey"}
    assert not by_kind(workflows_docs(), "Secret")


def test_the_endpoint_drops_its_scheme_as_argo_expects():
    _, repo = repository(workflows_docs())
    assert repo["s3"]["endpoint"] == "seaweedfs-filer.seaweedfs.svc.cluster.local:8333"


# --- the deployment ----------------------------------------------------------


def deployments(services):
    return docs_of(render("deployment-separate.j2", spec(services=services)))


def env_of(deployment):
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_every_pod_of_the_extension_runs_as_its_workflows_account():
    found = deployments(["workflows"])
    assert [d["metadata"]["name"] for d in found] == ["datasets-backend", "datasets-frontend"]
    for d in found:
        assert d["spec"]["template"]["spec"]["serviceAccountName"] == "datasets-workflows"


def test_the_extension_gets_the_variables_an_app_gets():
    for d in deployments(["workflows"]):
        e = env_of(d)
        assert e["WORKFLOWS_NAMESPACE"] == "thinkube-control"
        assert e["WORKFLOWS_SERVER_URL"] == "http://argo-workflows-server.argo.svc.cluster.local:2746"
        assert e["WORKFLOWS_SERVICE_ACCOUNT"] == "datasets-workflows"
        assert e["WORKFLOWS_UI_URL"] == "https://argo.thinkube.com/workflows/thinkube-control"
        assert e["WORKFLOWS_ARTIFACT_REPOSITORY"] == "datasets-artifact-repositories"
        assert e["CONTAINER_IMAGE_BACKEND"] == "registry.thinkube.com/thinkube/datasets-backend:latest"
        assert e["CONTAINER_IMAGE_FRONTEND"] == "registry.thinkube.com/thinkube/datasets-frontend:latest"


def test_an_extension_without_workflows_gets_no_account_and_no_variables():
    for d in deployments(["database"]):
        assert "serviceAccountName" not in d["spec"]["template"]["spec"]
        e = env_of(d)
        assert not [k for k in e if k.startswith("WORKFLOWS_")]
        assert not [k for k in e if k.startswith("CONTAINER_IMAGE_")]


# --- the kustomization -------------------------------------------------------


def test_an_extension_with_workflows_lists_workflows_yaml_and_no_namespace():
    assert kustomization_resources(
        is_knative=False, needs_storage=False, has_workflows=True, extension=True
    ) == [
        "app-metadata.yaml",
        "service-config.yaml",
        "deployments.yaml",
        "services.yaml",
        "ingress.yaml",
        "workflows.yaml",
        "argocd-postsync-hook.yaml",
    ]


def test_an_extension_without_workflows_lists_no_workflows_yaml():
    assert "workflows.yaml" not in kustomization_resources(
        is_knative=False, needs_storage=False, has_workflows=False, extension=True
    )
