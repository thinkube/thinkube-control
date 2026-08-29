"""Teardown for components deployed through the template process.

A template deployment leaves an ArgoCD application, a namespace, a Gitea
repository, a checkout on disk and a services row. Uninstalling one has to
remove all five, and has to keep going when a piece is already gone so a
half-removed component can still be cleaned up.
"""

import base64
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

import urllib3
from kubernetes import client
from kubernetes.client.rest import ApiException

from app.models.services import Service as ServiceModel

logger = logging.getLogger(__name__)

ARGOCD_NAMESPACE = "argocd"
GITEA_ORG = "thinkube-deployments"
COMPONENTS_DIR = "/home/thinkube/components"
APPS_DIR = "/home/thinkube/apps"


class ComponentTeardown:
    """Removes everything a template deployment created."""

    def __init__(self, db, domain: str):
        self.db = db
        self.domain = domain
        self.core_v1 = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

    def remove(self, name: str, namespace: str) -> Dict[str, Any]:
        """Remove a component. Returns what was done and what failed."""
        done: List[str] = []
        failed: List[str] = []

        for label, step in (
            ("argocd application", lambda: self._delete_argocd_app(name)),
            ("namespace", lambda: self._delete_namespace(namespace)),
            ("gitea repository", lambda: self._delete_gitea_repo(name)),
            ("checkout", lambda: self._delete_checkout(name)),
            ("service record", lambda: self._delete_service_row(name)),
        ):
            try:
                if step():
                    done.append(label)
            except Exception as e:
                logger.error(f"Teardown of {name} failed at {label}: {e}")
                failed.append(f"{label}: {e}")

        return {"removed": done, "failed": failed}

    def _delete_argocd_app(self, name: str) -> bool:
        try:
            self.custom.delete_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=ARGOCD_NAMESPACE,
                plural="applications",
                name=name,
            )
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def _delete_namespace(self, namespace: str) -> bool:
        try:
            self.core_v1.delete_namespace(namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def _gitea_token(self) -> str:
        secret = self.core_v1.read_namespaced_secret("gitea-admin-token", "gitea")
        return base64.b64decode(secret.data["token"]).decode()

    def _delete_gitea_repo(self, name: str) -> bool:
        http = urllib3.PoolManager(cert_reqs="CERT_NONE")
        urllib3.disable_warnings()
        response = http.request(
            "DELETE",
            f"https://git.{self.domain}/api/v1/repos/{GITEA_ORG}/{name}",
            headers={"Authorization": f"token {self._gitea_token()}"},
        )
        if response.status in (204, 200):
            return True
        if response.status == 404:
            return False
        raise RuntimeError(f"Gitea returned {response.status}")

    def _delete_checkout(self, name: str) -> bool:
        removed = False
        for base in (COMPONENTS_DIR, APPS_DIR):
            path = Path(base) / name
            if path.exists():
                shutil.rmtree(path)
                removed = True
        return removed

    def _delete_service_row(self, name: str) -> bool:
        service = (
            self.db.query(ServiceModel).filter(ServiceModel.name == name).first()
        )
        if not service:
            return False
        self.db.delete(service)
        self.db.commit()
        return True
