# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Deleting a user application deployed from a template.

A template deploy (scripts/deploy_application.py) leaves pieces in many
places: an Argo CD Application, a build WorkflowTemplate with its Secret and
its Workflows in argo, an SSH Secret in argocd, the app's namespace,
cluster-scoped MLflow volumes, a Keycloak client, images in Harbor, build
artifacts in Thinkube Storage, rows in Thinkube Control's own database, and the
checkout on disk. The Gitea repository and the PostgreSQL databases hold work
and data nothing can rebuild, so they are removed only when asked.

The steps run in a fixed order and each one reports what it did. A failed
step does not stop the next ones: the report then names everything that could
not be removed, so the delete can be run again or finished by hand. The Argo CD
Application goes first: with auto-sync and CreateNamespace it would otherwise
bring the namespace back.

Shared objects are never touched: the argo EventSource and Sensor, the argo
Secrets gitea-webhook-secret, harbor-docker-config and gitea-ssh-key, and the
API token file.
"""

import base64
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import certifi
import urllib3
import yaml
from httpx import HTTPStatusError
from kubernetes import client
from kubernetes.client.rest import ApiException

from app.models.deployments import DeploymentLog, TemplateDeployment
from app.models.service_schemas import ServiceValidation
from app.models.services import Service as ServiceModel
from app.services.app_secret_usage import record_usage
from app.services.component_teardown import APPS_DIR, ARGOCD_NAMESPACE, GITEA_ORG, ComponentTeardown
from app.services.deploy_identity import deploy_api_client
from app.services.harbor_client import HarborClient

sys.path.insert(0, "/home/thinkube/thinkube-control/scripts")
from manifest_plan import EXTENSION_NAMESPACE  # noqa: E402

logger = logging.getLogger(__name__)

# The deploy's helpers for the names it gives an app's objects.
SCRIPTS_DIR = "/home/thinkube/thinkube-control/scripts"

ARGO_NAMESPACE = "argo"
APP_LABEL = "thinkube.io/app-name"
# The Secret the deploy reads the admin credentials from (scripts/deploy_application.py).
ADMIN_SECRET = ("thinkube-control", "admin-credentials")
KEYCLOAK_REALM = "thinkube"
# Harbor's built-in administrator; Harbor does not allow renaming it.
HARBOR_ADMIN_USER = "admin"
HARBOR_PROJECT = "thinkube"
STORAGE_SECRET = ("seaweedfs", "seaweedfs-s3-credentials")
ARTIFACT_BUCKET = "argo-artifacts"
# SeaweedFS accepts any region; us-east-1 is its default, which a SigV4 signature needs.
STORAGE_REGION = "us-east-1"
POSTGRES = {"namespace": "postgres", "pod": "postgresql-official-0", "container": "postgres"}

NAMESPACE_WAIT_SECONDS = 180
NAMESPACE_POLL_SECONDS = 3

APP_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def refusal(db, name: str) -> Optional[str]:
    """Why the name cannot be deleted as a user app, or None when it can.

    A user app is a name with template deployment records whose
    deployment_type is user_app, none of them a component, and no services row
    of another type.
    """
    if not APP_NAME.match(name):
        return f"'{name}' is not an application name (lowercase letters, digits and hyphens)"
    if ServiceValidation.is_reserved_name(name):
        return f"'{name}' is a platform component, not an application"

    service = db.query(ServiceModel).filter(ServiceModel.name == name).first()
    if service is not None and service.type != "user_app":
        hint = (
            " Remove it with uninstall_optional_component."
            if service.type in ("component", "optional")
            else ""
        )
        return f"'{name}' is a {service.type} service, not a user application.{hint}"

    records = db.query(TemplateDeployment).filter(TemplateDeployment.name == name).all()
    types = {(r.variables or {}).get("deployment_type") for r in records}
    if "component" in types:
        return (
            f"'{name}' is a component deployed from a template, not a user application. "
            "Remove it with uninstall_optional_component."
        )
    if not types & {"user_app", "extension"}:
        return f"'{name}' has no deployment record of a user application deployed from a template"

    from app.services.run_queue import IN_FLIGHT

    running = [r for r in records if r.status in IN_FLIGHT]
    if running:
        return f"a deploy of '{name}' is {running[0].status} ({running[0].id}); delete the app when it has ended"
    return None


def _scripts() -> None:
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)


def _reason(error: Exception) -> str:
    """One line for a failed step. An ApiException's text carries every response header."""
    if isinstance(error, ApiException):
        try:
            message = json.loads(error.body or "{}").get("message")
        except ValueError:
            message = None
        return f"HTTP {error.status} {error.reason}" + (f": {message}" if message else "")
    return str(error)


def _decode(secret, key: str) -> str:
    if not secret.data or key not in secret.data:
        raise RuntimeError(f"Secret {secret.metadata.namespace}/{secret.metadata.name} has no key {key}")
    return base64.b64decode(secret.data[key]).decode()


class AppTeardown(ComponentTeardown):
    """Removes a user application and everything its template deploy created."""

    def __init__(self, db, domain: str):
        super().__init__(db, domain)
        # psql runs through a pod exec, which takes over the connection of the
        # client it is given, so it has a client of its own.
        self.exec_v1 = client.CoreV1Api(deploy_api_client())
        api = deploy_api_client()
        self.apps_v1 = client.AppsV1Api(api)
        self.batch_v1 = client.BatchV1Api(api)
        self.rbac_v1 = client.RbacAuthorizationV1Api(api)

    def remove_app(
        self, name: str, delete_repository: bool, delete_databases: bool
    ) -> Dict:
        """Run every step in order and report each one. Never raises for a failed step."""
        # An extension runs in Thinkube Control's namespace: its own objects
        # are removed, by label and by name, never the namespace
        extension = self._deployed_as_extension(name)
        namespace = EXTENSION_NAMESPACE if extension else name
        namespace_step = (
            (f"Objects of {name} in namespace {namespace}", lambda: self._extension_objects(name, namespace))
            if extension else (f"Namespace {name}", lambda: self._namespace_gone(name))
        )
        steps: List[Tuple[str, Callable[[], List[str]]]] = [
            ("Argo CD Application argocd/" + name, lambda: self._argocd_app(name)),
            (f"WorkflowTemplate argo/{name}-build", lambda: self._workflow_template(name)),
            (f"Secret argo/{self._build_secret(name)}", lambda: self._secret(ARGO_NAMESPACE, self._build_secret(name))),
            (f"Workflows in argo labelled {APP_LABEL}={name}", lambda: self._workflows(name)),
            (f"Secret argocd/gitea-{name}-ssh", lambda: self._secret(ARGOCD_NAMESPACE, f"gitea-{name}-ssh")),
            namespace_step,
            (f"PersistentVolumes {name}-<container>-mlflow-root", lambda: self._mlflow_volumes(name, namespace)),
            (f"Keycloak client {name} in realm {KEYCLOAK_REALM}", lambda: self._keycloak_client(name)),
            (f"Harbor repositories {HARBOR_PROJECT}/{name}-<container>", lambda: self._harbor_repositories(name)),
            (f"Objects under {name}/ in bucket {ARTIFACT_BUCKET}", lambda: self._artifacts(name)),
            (f"Thinkube Control services row {name}", lambda: self._service_row(name)),
            (f"Thinkube Control secret usage of {name}", lambda: self._secret_usage(name)),
        ]
        database_names = self._database_names(name)
        if delete_databases:
            for database in database_names:
                steps.append((f"PostgreSQL database {database}", lambda d=database: self._database(d)))
        repository = f"Gitea repository {GITEA_ORG}/{name}"
        if delete_repository:
            steps.append((repository, lambda: [f"{GITEA_ORG}/{name}"] if self._delete_gitea_repo(name) else []))
        # A second run needs the deployment record (it is what makes the name
        # an app) and the checkout (its thinkube.yaml names the containers), so
        # these go only once every other step has succeeded.
        final_steps: List[Tuple[str, Callable[[], List[str]]]] = [
            (f"Thinkube Control deployment records of {name}", lambda: self._deployment_records(name)),
            (f"Checkout {APPS_DIR}/{name}", lambda: self._app_checkout(name)),
        ]

        report = []
        for label, step in steps:
            try:
                removed = step()
                result = ("removed: " + ", ".join(removed)) if removed else "not found"
            except Exception as e:
                logger.error(f"Deleting app {name} failed at {label}: {e}")
                result = f"failed: {_reason(e)}"
            report.append({"step": label, "result": result})

        if any(s["result"].startswith("failed: ") for s in report):
            report += [{"step": label, "result": "kept: a step above failed; run delete_app again to finish"}
                       for label, _ in final_steps]
        else:
            for label, step in final_steps:
                try:
                    removed = step()
                    result = ("removed: " + ", ".join(removed)) if removed else "not found"
                except Exception as e:
                    logger.error(f"Deleting app {name} failed at {label}: {e}")
                    result = f"failed: {_reason(e)}"
                report.append({"step": label, "result": result})

        kept = []
        if not delete_databases:
            kept += [(f"PostgreSQL database {d}", "delete_databases is false") for d in database_names]
        if not delete_repository:
            kept.append((f"{repository} and its webhook", "delete_repository is false"))
        report += [{"step": item, "result": f"kept: {reason}"} for item, reason in kept]

        not_removed = [
            f"{s['step']}: {s['result'][len('failed: '):]}" for s in report if s["result"].startswith("failed: ")
        ]
        if not_removed:
            message = f"{name} is not fully deleted. Could not remove: " + "; ".join(not_removed) + "."
        else:
            message = f"{name} is deleted."
        if kept:
            message += " Kept: " + "; ".join(item for item, _ in kept) + "."
        return {
            "app": name,
            "status": "incomplete" if not_removed else "deleted",
            "message": message,
            "steps": report,
            "not_removed": not_removed,
            "kept": [item for item, _ in kept],
        }

    # Names the deploy gives

    @staticmethod
    def _build_secret(name: str) -> str:
        _scripts()
        from platform_credentials import build_secret_name

        return build_secret_name(name)

    @staticmethod
    def _database_names(name: str) -> List[str]:
        _scripts()
        from app_databases import database_names

        return database_names(name, tests_enabled=True)

    def _containers(self, name: str) -> List[str]:
        path = Path(APPS_DIR) / name / "thinkube.yaml"
        if not path.exists():
            raise RuntimeError(f"the container names are unknown: {path} does not exist")
        spec = (yaml.safe_load(path.read_text()) or {}).get("spec") or {}
        names = [c["name"] for c in spec.get("containers") or [] if c.get("name")]
        if not names:
            raise RuntimeError(f"the container names are unknown: {path} lists no spec.containers")
        return names

    # Kubernetes

    def _argocd_app(self, name: str) -> List[str]:
        return [f"argocd/{name}"] if self._delete_argocd_app(name) else []

    def _workflow_template(self, name: str) -> List[str]:
        try:
            self.custom.delete_namespaced_custom_object(
                group="argoproj.io", version="v1alpha1", namespace=ARGO_NAMESPACE,
                plural="workflowtemplates", name=f"{name}-build",
            )
            return [f"{name}-build"]
        except ApiException as e:
            if e.status == 404:
                return []
            raise

    def _secret(self, namespace: str, secret: str) -> List[str]:
        try:
            self.core_v1.delete_namespaced_secret(secret, namespace)
            return [f"{namespace}/{secret}"]
        except ApiException as e:
            if e.status == 404:
                return []
            raise

    def _workflows(self, name: str) -> List[str]:
        found = self.custom.list_namespaced_custom_object(
            group="argoproj.io", version="v1alpha1", namespace=ARGO_NAMESPACE,
            plural="workflows", label_selector=f"{APP_LABEL}={name}",
        )
        removed, failed = [], []
        for workflow in found.get("items", []):
            workflow_name = workflow["metadata"]["name"]
            try:
                self.custom.delete_namespaced_custom_object(
                    group="argoproj.io", version="v1alpha1", namespace=ARGO_NAMESPACE,
                    plural="workflows", name=workflow_name,
                )
                removed.append(workflow_name)
            except ApiException as e:
                if e.status != 404:
                    failed.append(f"{workflow_name} (HTTP {e.status} {e.reason})")
        if failed:
            raise RuntimeError(f"removed {len(removed)}, could not remove {', '.join(failed)}")
        return [f"{len(removed)} workflows"] if removed else []

    def _namespace_gone(self, name: str) -> List[str]:
        """Delete the namespace and wait, bounded, until Kubernetes has removed it."""
        try:
            deleted = self._delete_namespace(name)
        except ApiException as e:
            # 409: the namespace is already terminating; waiting for it is what is left.
            if e.status != 409:
                raise
            deleted = True
        deadline = time.monotonic() + NAMESPACE_WAIT_SECONDS
        while True:
            try:
                self.core_v1.read_namespace(name)
            except ApiException as e:
                if e.status == 404:
                    return [name] if deleted else []
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"still terminating after {NAMESPACE_WAIT_SECONDS} s; Kubernetes goes on "
                    f"removing it (kubectl get namespace {name} shows what holds it)"
                )
            time.sleep(NAMESPACE_POLL_SECONDS)

    def _deployed_as_extension(self, name: str) -> bool:
        records = self.db.query(TemplateDeployment).filter(TemplateDeployment.name == name).all()
        return any((r.variables or {}).get("deployment_type") == "extension" for r in records)

    def _extension_objects(self, name: str, namespace: str) -> List[str]:
        """The objects an extension's deploy made in Control's namespace: those
        labelled app.kubernetes.io/name=<name>, the four it names without
        the label, and the Argo workflows that run as its workflows account.
        Control's own objects there carry none of these."""
        selector = f"app.kubernetes.io/name={name}"
        removed: List[str] = []
        kinds = [
            ("Deployment", self.apps_v1.list_namespaced_deployment, self.apps_v1.delete_namespaced_deployment),
            ("Service", self.core_v1.list_namespaced_service, self.core_v1.delete_namespaced_service),
            ("PersistentVolumeClaim", self.core_v1.list_namespaced_persistent_volume_claim,
             self.core_v1.delete_namespaced_persistent_volume_claim),
            ("Secret", self.core_v1.list_namespaced_secret, self.core_v1.delete_namespaced_secret),
            ("ConfigMap", self.core_v1.list_namespaced_config_map, self.core_v1.delete_namespaced_config_map),
            ("Job", self.batch_v1.list_namespaced_job, self.batch_v1.delete_namespaced_job),
            ("ServiceAccount", self.core_v1.list_namespaced_service_account,
             self.core_v1.delete_namespaced_service_account),
            ("Role", self.rbac_v1.list_namespaced_role, self.rbac_v1.delete_namespaced_role),
            ("RoleBinding", self.rbac_v1.list_namespaced_role_binding, self.rbac_v1.delete_namespaced_role_binding),
        ]
        for kind, list_objects, delete in kinds:
            for item in list_objects(namespace, label_selector=selector).items:
                delete(item.metadata.name, namespace, propagation_policy="Background")
                removed.append(f"{kind} {item.metadata.name}")
        for route in self.custom.list_namespaced_custom_object(
            "gateway.networking.k8s.io", "v1", namespace, "httproutes", label_selector=selector
        ).get("items", []):
            self.custom.delete_namespaced_custom_object(
                "gateway.networking.k8s.io", "v1", namespace, "httproutes", route["metadata"]["name"]
            )
            removed.append(f"HTTPRoute {route['metadata']['name']}")
        # The extension's Argo workflows carry no label the platform sets; they
        # are the ones that run as its workflows account (workflows.j2).
        account = f"{name}-workflows"
        for workflow in self.custom.list_namespaced_custom_object(
            "argoproj.io", "v1alpha1", namespace, "workflows"
        ).get("items", []):
            if (workflow.get("spec") or {}).get("serviceAccountName") != account:
                continue
            try:
                self.custom.delete_namespaced_custom_object(
                    "argoproj.io", "v1alpha1", namespace, "workflows", workflow["metadata"]["name"]
                )
                removed.append(f"Workflow {workflow['metadata']['name']}")
            except ApiException as e:
                if e.status != 404:
                    raise
        named = [
            ("ConfigMap", self.core_v1.delete_namespaced_config_map, f"{name}-metadata"),
            ("ConfigMap", self.core_v1.delete_namespaced_config_map, f"{name}-service-config"),
            ("Job", self.batch_v1.delete_namespaced_job, f"{name}-deployment-completed"),
            ("Job", self.batch_v1.delete_namespaced_job, f"{name}-deployment-failed"),
        ]
        for kind, delete, object_name in named:
            try:
                delete(object_name, namespace, propagation_policy="Background")
                removed.append(f"{kind} {object_name}")
            except ApiException as e:
                if e.status != 404:
                    raise
        return removed

    def _mlflow_volumes(self, name: str, namespace: str) -> List[str]:
        """The MLflow root volumes the app's claims in its namespace were bound to.

        storage-pvc.j2 names them <app>-<container>-mlflow-root and puts no label
        on them; the claim reference says which app a volume belongs to, so a
        volume of an app whose name starts with this one is not taken.
        """
        removed = []
        for volume in self.core_v1.list_persistent_volume().items:
            volume_name = volume.metadata.name
            claim = volume.spec.claim_ref if volume.spec else None
            if not (volume_name.startswith(f"{name}-") and volume_name.endswith("-mlflow-root")):
                continue
            if claim is None or claim.namespace != namespace:
                continue
            try:
                self.core_v1.delete_persistent_volume(volume_name)
                removed.append(volume_name)
            except ApiException as e:
                if e.status != 404:
                    raise
        return removed

    # Services outside Kubernetes

    def _admin_credentials(self) -> Tuple[str, str]:
        secret = self.core_v1.read_namespaced_secret(ADMIN_SECRET[1], ADMIN_SECRET[0])
        return _decode(secret, "admin-username"), _decode(secret, "admin-password")

    def _keycloak_client(self, name: str) -> List[str]:
        """Delete the client the deploy created, as create_keycloak_client in scripts/deploy_application.py reaches Keycloak."""
        username, password = self._admin_credentials()
        keycloak = f"https://auth.{self.domain}"
        http = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())

        token_response = http.request(
            "POST",
            f"{keycloak}/realms/master/protocol/openid-connect/token",
            fields={"client_id": "admin-cli", "username": username, "password": password, "grant_type": "password"},
            encode_multipart=False,
        )
        if token_response.status != 200:
            raise RuntimeError(f"Keycloak refused the admin token: HTTP {token_response.status}")
        headers = {"Authorization": f"Bearer {json.loads(token_response.data)['access_token']}"}

        clients_url = f"{keycloak}/admin/realms/{KEYCLOAK_REALM}/clients"
        found = http.request("GET", clients_url, fields={"clientId": name}, headers=headers)
        if found.status != 200:
            raise RuntimeError(f"Keycloak did not list the clients: HTTP {found.status}")
        removed = []
        for kc_client in json.loads(found.data):
            if kc_client.get("clientId") != name:
                continue
            answer = http.request("DELETE", f"{clients_url}/{kc_client['id']}", headers=headers)
            if answer.status == 404:
                continue
            if answer.status != 204:
                raise RuntimeError(f"Keycloak refused to delete client {name}: HTTP {answer.status}")
            removed.append(name)
        return removed

    def _harbor_repositories(self, name: str) -> List[str]:
        """The image and its layer cache of each container, as build-workflow.j2 names them."""
        containers = self._containers(name)
        _, password = self._admin_credentials()
        removed = []
        with HarborClient(username=HARBOR_ADMIN_USER, password=password) as harbor:
            for container in containers:
                for repository in (f"{name}-{container}/cache", f"{name}-{container}"):
                    try:
                        harbor.delete_repository(HARBOR_PROJECT, repository)
                        removed.append(f"{HARBOR_PROJECT}/{repository}")
                    except HTTPStatusError as e:
                        if e.response.status_code != 404:
                            raise RuntimeError(
                                f"Harbor refused to delete {HARBOR_PROJECT}/{repository}: "
                                f"HTTP {e.response.status_code}"
                            )
        return removed

    def _storage_client(self):
        import boto3
        from botocore.config import Config

        secret = self.core_v1.read_namespaced_secret(STORAGE_SECRET[1], STORAGE_SECRET[0])
        return boto3.client(
            "s3",
            endpoint_url=_decode(secret, "endpoint_internal"),
            aws_access_key_id=_decode(secret, "access_key"),
            aws_secret_access_key=_decode(secret, "secret_key"),
            region_name=STORAGE_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def _artifacts(self, name: str) -> List[str]:
        """The build logs and artifacts the app's workflows archived (workflows.j2 keyFormat <app>/...)."""
        storage = self._storage_client()
        keys = []
        for page in storage.get_paginator("list_objects_v2").paginate(Bucket=ARTIFACT_BUCKET, Prefix=f"{name}/"):
            keys += [o["Key"] for o in page.get("Contents", [])]
        for key in keys:
            storage.delete_object(Bucket=ARTIFACT_BUCKET, Key=key)
        return [f"{len(keys)} objects"] if keys else []

    def _database(self, database: str) -> List[str]:
        """Drop one database through psql in the PostgreSQL pod, as manage_databases creates it."""
        _scripts()
        from app_databases import drop_statement, exists, exists_query

        admin_username, _ = self._admin_credentials()
        if not exists(self._psql(admin_username, exists_query(database))):
            return []
        output = self._psql(admin_username, drop_statement(database))
        if exists(self._psql(admin_username, exists_query(database))):
            raise RuntimeError(f"it still exists after DROP DATABASE: {output.strip()}")
        return [database]

    def _psql(self, admin_username: str, statement: str) -> str:
        from kubernetes.stream import stream

        return stream(
            self.exec_v1.connect_get_namespaced_pod_exec,
            POSTGRES["pod"],
            POSTGRES["namespace"],
            container=POSTGRES["container"],
            command=["psql", "-U", admin_username, "-d", "postgres", "-tA", "-c", statement],
            stderr=True, stdin=False, stdout=True, tty=False,
            _request_timeout=60,
        )

    # Thinkube Control's own records

    def _service_row(self, name: str) -> List[str]:
        return [name] if self._delete_service_row(name) else []

    def _deployment_records(self, name: str) -> List[str]:
        """The app's deployment rows and their logs; the logs' foreign key has no ON DELETE."""
        ids = [r.id for r in self.db.query(TemplateDeployment.id).filter(TemplateDeployment.name == name).all()]
        if not ids:
            return []
        logs = (
            self.db.query(DeploymentLog)
            .filter(DeploymentLog.deployment_id.in_(ids))
            .delete(synchronize_session=False)
        )
        self.db.query(TemplateDeployment).filter(TemplateDeployment.id.in_(ids)).delete(
            synchronize_session=False
        )
        self.db.commit()
        return [f"{len(ids)} deployments", f"{logs} log lines"]

    def _secret_usage(self, name: str) -> List[str]:
        from app.models.secrets import AppSecret

        used = self.db.query(AppSecret).filter(AppSecret.app_name == name).count()
        if not used:
            return []
        record_usage(self.db, name, [])
        return [f"{used} rows"]

    def _app_checkout(self, name: str) -> List[str]:
        path = Path(APPS_DIR) / name
        if not path.exists():
            return []
        shutil.rmtree(path)
        return [str(path)]
