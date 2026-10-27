# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Deleting a user app removes what its deploy created, in order, and reports every step.

The Argo CD Application goes before the namespace, so auto-sync cannot bring
the namespace back. A failed step does not stop the next ones. The Gitea
repository and the databases are kept unless asked for. Components and names
without a user-app deployment are refused.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from kubernetes import client
from kubernetes.client.rest import ApiException

import app.api.templates as templates
import app.services.app_teardown as app_teardown
import app.services.component_teardown as component_teardown
from app.models.deployments import TemplateDeployment
from app.models.services import Service as ServiceModel
from app.services.harbor_client import HarborClient

REPO_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class FakeDB:
    """Answers the refusal's two queries: the services row and the deployment records."""

    def __init__(self, service=None, deployments=()):
        self.service = service
        self.deployments = list(deployments)

    def query(self, model):
        if model is ServiceModel:
            return Query([self.service] if self.service else [])
        if model is TemplateDeployment:
            return Query(self.deployments)
        raise AssertionError(f"unexpected query of {model}")


def deployment(deployment_type, status="success"):
    return SimpleNamespace(id="d1", status=status, variables={"deployment_type": deployment_type})


def not_found():
    return ApiException(status=404, reason="Not Found")


class FakeCore:
    def __init__(self, calls):
        self.calls = calls

    def delete_namespaced_secret(self, name, namespace):
        self.calls.append(f"delete secret {namespace}/{name}")

    def delete_namespace(self, name):
        self.calls.append(f"delete namespace {name}")

    def read_namespace(self, name):
        raise not_found()

    def list_persistent_volume(self):
        return SimpleNamespace(items=[])


class FakeCustom:
    def __init__(self, calls):
        self.calls = calls

    def delete_namespaced_custom_object(self, group, version, namespace, plural, name):
        self.calls.append(f"delete {plural} {namespace}/{name}")

    def list_namespaced_custom_object(self, group, version, namespace, plural, label_selector):
        return {"items": []}


@pytest.fixture
def teardown(monkeypatch):
    """An AppTeardown on fake Kubernetes clients, with the steps outside Kubernetes recorded."""
    monkeypatch.setattr(component_teardown, "deploy_api_client", lambda: client.ApiClient())
    monkeypatch.setattr(app_teardown, "deploy_api_client", lambda: client.ApiClient())
    monkeypatch.setattr(app_teardown, "SCRIPTS_DIR", REPO_SCRIPTS)
    calls = []
    t = app_teardown.AppTeardown(db=None, domain="example.com")
    t.core_v1 = FakeCore(calls)
    t.custom = FakeCustom(calls)

    def recorded(label):
        def step(*args):
            calls.append(label)
            return [label]
        return step

    for method in (
        "_keycloak_client", "_harbor_repositories", "_artifacts", "_service_row",
        "_deployment_records", "_secret_usage", "_app_checkout", "_database",
    ):
        monkeypatch.setattr(t, method, recorded(method))
    monkeypatch.setattr(t, "_delete_gitea_repo", lambda name: calls.append("_delete_gitea_repo") or True)
    monkeypatch.setattr(t, "_deployed_as_extension", lambda name: False)
    t.calls = calls
    return t


def results(report):
    return {s["step"]: s["result"] for s in report["steps"]}


def test_a_component_deployed_from_a_template_is_refused():
    db = FakeDB(deployments=[deployment("component")])
    assert "uninstall_optional_component" in app_teardown.refusal(db, "vllm")


def test_a_component_service_is_refused():
    db = FakeDB(service=SimpleNamespace(type="component"), deployments=[deployment("user_app")])
    assert "component service" in app_teardown.refusal(db, "vllm")


def test_a_platform_component_is_refused():
    assert "platform component" in app_teardown.refusal(FakeDB(), "keycloak")


def test_a_name_with_no_deployment_record_is_refused():
    assert "no deployment record" in app_teardown.refusal(FakeDB(), "nothing-here")


def test_an_app_whose_deploy_is_running_is_refused():
    db = FakeDB(deployments=[deployment("user_app", status="running")])
    assert "running" in app_teardown.refusal(db, "notes")


def test_a_user_app_is_accepted():
    db = FakeDB(service=SimpleNamespace(type="user_app"), deployments=[deployment("user_app")])
    assert app_teardown.refusal(db, "notes") is None


def test_the_route_answers_409_naming_the_reason():
    with pytest.raises(HTTPException) as refused:
        asyncio.run(templates.delete_app("nothing-here", False, False, db=FakeDB(), current_user={}))
    assert refused.value.status_code == 409
    assert "no deployment record" in refused.value.detail


def test_the_argocd_application_goes_before_the_namespace(teardown):
    report = teardown.remove_app("notes", delete_repository=False, delete_databases=False)
    calls = teardown.calls
    assert calls.index("delete applications argocd/notes") < calls.index("delete namespace notes")
    assert calls.index("delete workflowtemplates argo/notes-build") < calls.index("delete namespace notes")
    assert "delete secret argo/notes-build-credentials" in calls
    assert "delete secret argocd/gitea-notes-ssh" in calls
    assert calls.index("delete namespace notes") < calls.index("_keycloak_client")
    assert calls[-1] == "_app_checkout"
    assert report["status"] == "deleted" and report["not_removed"] == []


def test_a_failed_step_does_not_stop_the_next_ones(teardown, monkeypatch):
    def keycloak_down(name):
        raise RuntimeError("Keycloak refused the admin token: HTTP 401")

    monkeypatch.setattr(teardown, "_keycloak_client", keycloak_down)
    report = teardown.remove_app("notes", delete_repository=False, delete_databases=False)

    assert results(report)["Keycloak client notes in realm thinkube"] == (
        "failed: Keycloak refused the admin token: HTTP 401"
    )
    for later in ("_harbor_repositories", "_artifacts", "_service_row"):
        assert later in teardown.calls
    # The record and the checkout stay, so a second run is accepted and
    # finds the containers to remove.
    for kept in ("_deployment_records", "_app_checkout"):
        assert kept not in teardown.calls
    assert results(report)["Thinkube Control deployment records of notes"].startswith("kept: ")
    assert results(report)["Checkout /home/thinkube/apps/notes"].startswith("kept: ")
    assert report["status"] == "incomplete"
    assert report["not_removed"] == [
        "Keycloak client notes in realm thinkube: Keycloak refused the admin token: HTTP 401"
    ]
    assert "Keycloak client notes" in report["message"]


def test_a_step_with_nothing_left_reports_not_found(teardown, monkeypatch):
    def gone(name, namespace):
        raise not_found()

    monkeypatch.setattr(teardown.core_v1, "delete_namespaced_secret", gone)
    report = teardown.remove_app("notes", delete_repository=False, delete_databases=False)
    assert results(report)["Secret argocd/gitea-notes-ssh"] == "not found"
    assert report["status"] == "deleted"


def test_repository_and_databases_are_kept_by_default(teardown):
    report = teardown.remove_app("notes", delete_repository=False, delete_databases=False)

    assert "_database" not in teardown.calls and "_delete_gitea_repo" not in teardown.calls
    steps = results(report)
    assert steps["PostgreSQL database notes"] == "kept: delete_databases is false"
    assert steps["PostgreSQL database test_notes"] == "kept: delete_databases is false"
    assert steps["Gitea repository thinkube-deployments/notes and its webhook"] == "kept: delete_repository is false"
    assert "Kept:" in report["message"]


def test_repository_and_databases_go_when_asked(teardown):
    report = teardown.remove_app("my-notes", delete_repository=True, delete_databases=True)

    assert teardown.calls.count("_database") == 2 and "_delete_gitea_repo" in teardown.calls
    steps = results(report)
    assert steps["PostgreSQL database my_notes"].startswith("removed")
    assert steps["PostgreSQL database test_my_notes"].startswith("removed")
    assert steps["Gitea repository thinkube-deployments/my-notes"] == "removed: thinkube-deployments/my-notes"
    assert report["kept"] == []


def test_mlflow_volumes_of_another_app_with_a_longer_name_are_kept(teardown):
    def volume(name, namespace):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name),
            spec=SimpleNamespace(claim_ref=SimpleNamespace(namespace=namespace)),
        )

    deleted = []
    teardown.core_v1.list_persistent_volume = lambda: SimpleNamespace(items=[
        volume("notes-web-mlflow-root", "notes"),
        volume("notes-admin-web-mlflow-root", "notes-admin"),
        volume("notes-web-storage", "notes"),
    ])
    teardown.core_v1.delete_persistent_volume = deleted.append
    assert teardown._mlflow_volumes("notes", "notes") == ["notes-web-mlflow-root"]
    assert deleted == ["notes-web-mlflow-root"]


def test_harbor_encodes_a_slash_in_a_repository_name_twice():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.raw_path))
        return httpx.Response(200)

    harbor = HarborClient(base_url="https://registry.example.com", username="admin", password="p")
    harbor.client = httpx.Client(transport=httpx.MockTransport(handler))
    harbor.delete_repository("thinkube", "notes-web/cache")
    harbor.delete_repository("thinkube", "notes-web")

    assert seen == [
        ("DELETE", b"/api/v2.0/projects/thinkube/repositories/notes-web%252Fcache"),
        ("DELETE", b"/api/v2.0/projects/thinkube/repositories/notes-web"),
    ]


def test_harbor_requests_do_not_send_back_its_session_cookie():
    """A request with Harbor's session cookie needs a CSRF token; Basic auth alone does not."""
    cookies_sent = []

    def handler(request):
        cookies_sent.append(request.headers.get("cookie"))
        return httpx.Response(200, headers={"set-cookie": "sid=abc; Path=/"})

    harbor = HarborClient(base_url="https://registry.example.com", username="admin", password="p")
    harbor.client = httpx.Client(transport=httpx.MockTransport(handler))
    harbor.delete_repository("thinkube", "notes-web/cache")
    harbor.delete_repository("thinkube", "notes-web")

    assert cookies_sent == [None, None]


def test_harbor_repositories_are_the_image_and_cache_of_each_container(teardown, monkeypatch, tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "thinkube.yaml").write_text(
        "spec:\n  containers:\n    - name: web\n    - name: api\n"
    )
    monkeypatch.setattr(app_teardown, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(teardown, "_admin_credentials", lambda: ("tkadmin", "secret"))
    deleted = []

    class FakeHarbor:
        def __init__(self, username, password):
            assert (username, password) == ("admin", "secret")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def delete_repository(self, project, repository):
            if repository == "notes-api/cache":
                request = httpx.Request("DELETE", "https://registry.example.com")
                raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
            deleted.append(f"{project}/{repository}")

    monkeypatch.setattr(app_teardown, "HarborClient", FakeHarbor)
    removed = app_teardown.AppTeardown._harbor_repositories(teardown, "notes")
    assert removed == ["thinkube/notes-web/cache", "thinkube/notes-web", "thinkube/notes-api"]
    assert deleted == removed


def test_an_extension_loses_its_own_objects_in_controls_namespace_and_nothing_else(teardown):
    """Only what carries the extension's label, and the four objects named after it."""
    selector_seen, deleted = set(), []

    def listing(kind, names):
        def list_objects(namespace, label_selector):
            assert namespace == "thinkube-control"
            selector_seen.add(label_selector)
            return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name=n)) for n in names])
        return list_objects

    def deleting(kind):
        def delete(name, namespace, propagation_policy=None):
            assert namespace == "thinkube-control"
            deleted.append(f"{kind} {name}")
        return delete

    teardown.apps_v1 = SimpleNamespace(
        list_namespaced_deployment=listing("Deployment", ["forge-backend"]),
        delete_namespaced_deployment=deleting("Deployment"))
    teardown.core_v1 = SimpleNamespace(
        list_namespaced_service=listing("Service", ["forge-backend"]),
        delete_namespaced_service=deleting("Service"),
        list_namespaced_persistent_volume_claim=listing("PVC", []),
        delete_namespaced_persistent_volume_claim=deleting("PVC"),
        list_namespaced_secret=listing("Secret", ["forge-secrets"]),
        delete_namespaced_secret=deleting("Secret"),
        list_namespaced_config_map=listing("ConfigMap", []),
        delete_namespaced_config_map=deleting("ConfigMap"),
        list_namespaced_service_account=listing("ServiceAccount", []),
        delete_namespaced_service_account=deleting("ServiceAccount"))
    teardown.batch_v1 = SimpleNamespace(
        list_namespaced_job=listing("Job", []), delete_namespaced_job=deleting("Job"))
    teardown.rbac_v1 = SimpleNamespace(
        list_namespaced_role=listing("Role", []), delete_namespaced_role=deleting("Role"),
        list_namespaced_role_binding=listing("RoleBinding", []),
        delete_namespaced_role_binding=deleting("RoleBinding"))
    teardown.custom = SimpleNamespace(
        list_namespaced_custom_object=lambda group, version, namespace, plural, label_selector=None: {"items": []},
        delete_namespaced_custom_object=None)

    teardown._extension_objects("forge", "thinkube-control")
    assert selector_seen == {"app.kubernetes.io/name=forge"}
    assert deleted == [
        "Deployment forge-backend", "Service forge-backend", "Secret forge-secrets",
        "ConfigMap forge-metadata", "ConfigMap forge-service-config",
        "Job forge-deployment-completed", "Job forge-deployment-failed",
    ]


def test_an_extension_loses_the_workflows_that_run_as_its_account_and_no_others(teardown):
    """Its workflows carry no platform label; the account they run as says whose they are."""
    deleted = []

    def nothing(namespace, label_selector):
        return SimpleNamespace(items=[])

    def no_named_object(name, namespace, propagation_policy=None):
        raise ApiException(status=404, reason="Not Found")

    teardown.apps_v1 = SimpleNamespace(list_namespaced_deployment=nothing, delete_namespaced_deployment=None)
    teardown.core_v1 = SimpleNamespace(
        list_namespaced_service=nothing, delete_namespaced_service=None,
        list_namespaced_persistent_volume_claim=nothing, delete_namespaced_persistent_volume_claim=None,
        list_namespaced_secret=nothing, delete_namespaced_secret=None,
        list_namespaced_config_map=nothing, delete_namespaced_config_map=no_named_object,
        list_namespaced_service_account=nothing, delete_namespaced_service_account=None)
    teardown.batch_v1 = SimpleNamespace(list_namespaced_job=nothing, delete_namespaced_job=no_named_object)
    teardown.rbac_v1 = SimpleNamespace(
        list_namespaced_role=nothing, delete_namespaced_role=None,
        list_namespaced_role_binding=nothing, delete_namespaced_role_binding=None)

    workflows = [
        {"metadata": {"name": "datasets-run-abc"}, "spec": {"serviceAccountName": "datasets-workflows"}},
        {"metadata": {"name": "forge-train-xyz"}, "spec": {"serviceAccountName": "forge-workflows"}},
        {"metadata": {"name": "control-run"}, "spec": {}},
    ]

    def list_objects(group, version, namespace, plural, label_selector=None):
        assert namespace == "thinkube-control"
        return {"items": workflows if plural == "workflows" else []}

    def delete_object(group, version, namespace, plural, name):
        assert (group, plural, namespace) == ("argoproj.io", "workflows", "thinkube-control")
        deleted.append(name)

    teardown.custom = SimpleNamespace(
        list_namespaced_custom_object=list_objects, delete_namespaced_custom_object=delete_object)

    removed = teardown._extension_objects("datasets", "thinkube-control")
    assert deleted == ["datasets-run-abc"]
    assert removed == ["Workflow datasets-run-abc"]
