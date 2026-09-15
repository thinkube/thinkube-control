#!/usr/bin/env python3
"""The workflows service: what an application gets for declaring it.

These render the real templates in templates/k8s/ with the same variables
the generator passes, so what is asserted here is what lands in k8s/.
"""

import json
import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

from manifest_plan import kustomization_content, kustomization_resources  # noqa: E402
from thinkube_yaml_validator import validate_knative_constraints  # noqa: E402


def env():
    """The same environment the generator builds, filters included."""
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    environment.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    environment.filters["to_json"] = lambda x: json.dumps(x)
    return environment


def spec(services=None, containers=None, deployment=None):
    return {
        "apiVersion": "thinkube.io/v1",
        "kind": "ThinkubeDeployment",
        "metadata": {"name": "wf-check"},
        "spec": {
            "deployment": deployment or {"type": "app", "replicas": 1},
            "containers": containers
            or [
                {"name": "backend", "build": "./backend", "port": 8000, "health": "/health"},
                {"name": "frontend", "build": "./frontend", "port": 80, "health": "/health"},
            ],
            **({"services": services} if services is not None else {}),
        },
    }


def template_vars(config):
    return {
        "project_name": "wf-check",
        "k8s_namespace": "wf-check",
        "domain_name": "thinkube.com",
        "container_registry": "registry.thinkube.com",
        "admin_username": "tkadmin",
        "admin_password": "secret",
        "thinkube_spec": config,
        "manifest_params": {},
        "seaweedfs_access_key": "AKIAEXAMPLE",
        "seaweedfs_password": "s3cr3t",
        "seaweedfs_endpoint": "http://seaweedfs-filer.seaweedfs.svc.cluster.local:8333",
    }


def render(name, config):
    return env().get_template(name).render(**template_vars(config))


def docs_of(text):
    return [d for d in yaml.safe_load_all(text) if d]


def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


# --- workflows.yaml ----------------------------------------------------------


@pytest.fixture
def workflows_docs():
    return docs_of(render("workflows.j2", spec(services=["database", "workflows"])))


def test_it_renders_a_service_account_role_and_binding(workflows_docs):
    kinds = [d["kind"] for d in workflows_docs]
    assert kinds.count("ServiceAccount") == 1
    assert kinds.count("Role") == 1
    assert kinds.count("RoleBinding") == 1


def test_everything_lands_in_the_application_namespace(workflows_docs):
    for d in workflows_docs:
        assert d["metadata"]["namespace"] == "wf-check", d["kind"]


def test_the_account_is_named_for_the_application(workflows_docs):
    sa = by_kind(workflows_docs, "ServiceAccount")[0]
    assert sa["metadata"]["name"] == "wf-check-workflows"


def test_the_binding_joins_that_role_to_that_account(workflows_docs):
    binding = by_kind(workflows_docs, "RoleBinding")[0]
    assert binding["roleRef"]["kind"] == "Role"
    assert binding["roleRef"]["name"] == "wf-check-workflows"
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "wf-check-workflows", "namespace": "wf-check"}
    ]


def test_the_role_lets_a_submitter_run_and_read_workflows(workflows_docs):
    role = by_kind(workflows_docs, "Role")[0]
    rule = next(
        r for r in role["rules"] if "workflows" in r.get("resources", [])
    )
    assert set(rule["apiGroups"]) == {"argoproj.io"}
    assert {"workflows", "workflowtemplates", "cronworkflows"} <= set(rule["resources"])
    assert {"get", "list", "watch", "create", "update", "patch", "delete"} <= set(
        rule["verbs"]
    )


def test_the_role_lets_the_executor_report_each_step(workflows_docs):
    """Without this the executor cannot write back what a step produced."""
    role = by_kind(workflows_docs, "Role")[0]
    rule = next(
        r for r in role["rules"] if "workflowtaskresults" in r.get("resources", [])
    )
    assert {"create", "patch"} <= set(rule["verbs"])


def test_the_role_reaches_pods_and_their_logs(workflows_docs):
    role = by_kind(workflows_docs, "Role")[0]
    rule = next(r for r in role["rules"] if "pods/log" in r.get("resources", []))
    assert rule["apiGroups"] == [""]
    assert {"get", "list", "watch", "patch"} <= set(rule["verbs"])


def test_the_role_grants_nothing_outside_the_namespace(workflows_docs):
    """A Role is namespaced; nothing here may reach the platform's builds."""
    role = by_kind(workflows_docs, "Role")[0]
    assert role["kind"] == "Role"
    for rule in role["rules"]:
        assert "clusterroles" not in rule.get("resources", [])
        assert "*" not in rule.get("resources", [])
        assert "*" not in rule.get("verbs", [])


# --- artifacts ---------------------------------------------------------------


def test_artifacts_go_to_thinkube_storage_under_this_app_prefix(workflows_docs):
    cm = by_kind(workflows_docs, "ConfigMap")[0]
    assert cm["metadata"]["name"] == "artifact-repositories"
    repo = yaml.safe_load(cm["data"]["default-v1"])
    assert repo["s3"]["bucket"] == "argo-artifacts"
    assert repo["s3"]["keyFormat"].startswith("wf-check/")


def test_the_endpoint_drops_its_scheme_as_argo_expects(workflows_docs):
    cm = by_kind(workflows_docs, "ConfigMap")[0]
    repo = yaml.safe_load(cm["data"]["default-v1"])
    assert repo["s3"]["endpoint"] == "seaweedfs-filer.seaweedfs.svc.cluster.local:8333"
    assert "://" not in repo["s3"]["endpoint"]


def test_the_key_format_keeps_argos_own_placeholders(workflows_docs):
    """Jinja must not eat {{workflow.name}}; Argo expands it at run time."""
    cm = by_kind(workflows_docs, "ConfigMap")[0]
    repo = yaml.safe_load(cm["data"]["default-v1"])
    assert "{{workflow.name}}" in repo["s3"]["keyFormat"]
    assert "{{pod.name}}" in repo["s3"]["keyFormat"]


def test_the_repository_names_the_secret_rendered_beside_it(workflows_docs):
    cm = by_kind(workflows_docs, "ConfigMap")[0]
    repo = yaml.safe_load(cm["data"]["default-v1"])
    secret = by_kind(workflows_docs, "Secret")[0]
    assert repo["s3"]["accessKeySecret"]["name"] == secret["metadata"]["name"]
    assert repo["s3"]["secretKeySecret"]["name"] == secret["metadata"]["name"]
    assert secret["stringData"]["accesskey"] == "AKIAEXAMPLE"
    assert secret["stringData"]["secretkey"] == "s3cr3t"


# --- the deployment ----------------------------------------------------------


def deployments(services):
    return docs_of(render("deployment-separate.j2", spec(services=services)))


def env_of(deployment):
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_every_pod_runs_as_the_workflows_account():
    for d in deployments(["workflows"]):
        assert d["spec"]["template"]["spec"]["serviceAccountName"] == "wf-check-workflows"


def test_the_variables_reach_every_container():
    for d in deployments(["workflows"]):
        e = env_of(d)
        assert e["WORKFLOWS_NAMESPACE"] == "wf-check"
        assert (
            e["WORKFLOWS_SERVER_URL"]
            == "http://argo-workflows-server.argo.svc.cluster.local:2746"
        )
        assert e["WORKFLOWS_SERVICE_ACCOUNT"] == "wf-check-workflows"
        assert e["WORKFLOWS_UI_URL"] == "https://argo.thinkube.com/workflows/wf-check"


def test_each_container_image_is_named_so_a_step_can_run_in_it():
    for d in deployments(["workflows"]):
        e = env_of(d)
        assert (
            e["CONTAINER_IMAGE_BACKEND"]
            == "registry.thinkube.com/thinkube/wf-check-backend:latest"
        )
        assert (
            e["CONTAINER_IMAGE_FRONTEND"]
            == "registry.thinkube.com/thinkube/wf-check-frontend:latest"
        )


def test_a_hyphenated_container_name_becomes_a_legal_variable():
    config = spec(
        services=["workflows"],
        containers=[{"name": "api-server", "build": ".", "port": 8000, "health": "/health"}],
    )
    d = docs_of(render("deployment-separate.j2", config))[0]
    assert "CONTAINER_IMAGE_API_SERVER" in env_of(d)


def test_the_image_pull_secret_stays_where_it_was():
    for d in deployments(["workflows"]):
        assert d["spec"]["template"]["spec"]["imagePullSecrets"] == [
            {"name": "app-pull-secret"}
        ]


# --- an application that did not ask for it ----------------------------------


def test_without_the_service_no_account_is_set():
    for d in deployments(["database"]):
        assert "serviceAccountName" not in d["spec"]["template"]["spec"]


def test_without_the_service_no_variables_are_injected():
    for d in deployments(["database"]):
        e = env_of(d)
        assert not [k for k in e if k.startswith("WORKFLOWS_")]
        assert not [k for k in e if k.startswith("CONTAINER_IMAGE_")]


def test_without_any_services_at_all_nothing_changes():
    for d in docs_of(render("deployment-separate.j2", spec())):
        assert "serviceAccountName" not in d["spec"]["template"]["spec"]
        assert not [k for k in env_of(d) if k.startswith("WORKFLOWS_")]


# --- the kustomization -------------------------------------------------------


def test_workflows_yaml_is_listed_when_declared():
    resources = kustomization_resources(
        is_knative=False, has_database=True, needs_storage=False, has_workflows=True
    )
    assert "workflows.yaml" in resources


def test_workflows_yaml_is_absent_when_not_declared():
    resources = kustomization_resources(
        is_knative=False, has_database=True, needs_storage=False, has_workflows=False
    )
    assert "workflows.yaml" not in resources


def test_the_post_sync_hook_stays_last():
    resources = kustomization_resources(
        is_knative=False, has_database=True, needs_storage=True, has_workflows=True
    )
    assert resources[-1] == "argocd-postsync-hook.yaml"


def test_the_existing_order_is_unchanged_for_an_app_without_workflows():
    assert kustomization_resources(
        is_knative=False, has_database=True, needs_storage=True, has_workflows=False
    ) == [
        "namespace.yaml",
        "resource-policies.yaml",
        "mlflow-secrets.yaml",
        "app-metadata.yaml",
        "deployments.yaml",
        "services.yaml",
        "ingress.yaml",
        "postgresql.yaml",
        "storage-pvc.yaml",
        "argocd-postsync-hook.yaml",
    ]


def test_a_knative_app_still_lists_its_own_resources():
    assert kustomization_resources(
        is_knative=True, has_database=False, needs_storage=False, has_workflows=False
    ) == [
        "namespace.yaml",
        "resource-policies.yaml",
        "mlflow-secrets.yaml",
        "app-metadata.yaml",
        "knative-service.yaml",
        "argocd-postsync-hook.yaml",
    ]


def _kustomization(has_workflows):
    return yaml.safe_load(kustomization_content(
        app_name="wf-check",
        container_registry="registry.thinkube.com",
        containers=[{"name": "backend"}, {"name": "api-server"}],
        resources=["deployments.yaml"],
        has_workflows=has_workflows,
    ))


def test_the_step_image_variable_follows_the_image_the_deployment_runs():
    doc = _kustomization(True)
    assert doc["images"] == [
        {"name": "registry.thinkube.com/thinkube/wf-check-backend", "newTag": "latest"},
        {"name": "registry.thinkube.com/thinkube/wf-check-api-server", "newTag": "latest"},
    ]
    by_source = {r["source"]["name"]: r for r in doc["replacements"]}
    backend = by_source["wf-check-backend"]
    assert backend["source"] == {
        "kind": "Deployment", "name": "wf-check-backend",
        "fieldPath": "spec.template.spec.containers.[name=backend].image",
    }
    assert [t["select"]["name"] for t in backend["targets"]] == ["wf-check-backend", "wf-check-api-server"]
    assert backend["targets"][1]["fieldPaths"] == [
        "spec.template.spec.containers.[name=api-server].env.[name=CONTAINER_IMAGE_BACKEND].value"
    ]
    assert by_source["wf-check-api-server"]["targets"][0]["fieldPaths"] == [
        "spec.template.spec.containers.[name=backend].env.[name=CONTAINER_IMAGE_API_SERVER].value"
    ]


def test_without_workflows_the_kustomization_has_no_replacements():
    doc = _kustomization(False)
    assert "replacements" not in doc
    assert doc["resources"] == ["deployments.yaml"]


# --- knative refuses it ------------------------------------------------------


def test_knative_with_workflows_is_refused():
    config = spec(services=["workflows"], deployment={"type": "knative"})
    config["spec"]["containers"] = [config["spec"]["containers"][0]]
    violations = validate_knative_constraints(config)
    assert violations == [
        "Service 'workflows': workflows is not allowed in Knative services. "
        "database, cache, and queue are allowed."
    ]


def test_knative_still_refuses_storage_in_the_same_words():
    config = spec(services=["storage"], deployment={"type": "knative"})
    config["spec"]["containers"] = [config["spec"]["containers"][0]]
    assert validate_knative_constraints(config) == [
        "Service 'storage': storage is not allowed in Knative services. "
        "database, cache, and queue are allowed."
    ]


def test_knative_accepts_the_services_it_always_did():
    config = spec(services=["database", "cache", "queue"], deployment={"type": "knative"})
    config["spec"]["containers"] = [config["spec"]["containers"][0]]
    assert validate_knative_constraints(config) == []
