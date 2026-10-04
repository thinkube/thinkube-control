# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The platform credentials an application's workloads read.

The database, Thinkube Experiments, Thinkube Storage and the build's clone of
the repository each need a credential the platform already holds. They reach
an application as Kubernetes Secrets written through the API at deploy time and
named, never inlined, in the manifests under k8s/, so the generated files a
repository commits carry no credential.

Both deploy paths use this module: the first deploy in deploy_application.py
and regeneration in manifest_generator.py.
"""

from dataclasses import dataclass
from typing import Dict, List

# Files earlier generators wrote into k8s/; generation removes them.
RETIRED_MANIFESTS = ('postgresql.yaml', 'mlflow-secrets.yaml')

BUILD_NAMESPACE = 'argo'


@dataclass(frozen=True)
class PlatformValues:
    admin_username: str
    admin_password: str
    mlflow_keycloak_token_url: str
    mlflow_keycloak_client_id: str
    mlflow_client_secret: str
    mlflow_username: str
    mlflow_password: str
    seaweedfs_endpoint: str
    seaweedfs_access_key: str
    seaweedfs_secret_key: str


def db_secret_name(app_name: str) -> str:
    return f'{app_name}-db-credentials'


def mlflow_secret_name(app_name: str) -> str:
    return f'{app_name}-mlflow-credentials'


def storage_secret_name(app_name: str) -> str:
    return f'{app_name}-storage-credentials'


def build_secret_name(app_name: str) -> str:
    return f'{app_name}-build-credentials'


ARTIFACT_SECRET_NAME = 'argo-artifacts-s3'


def database_name(app_name: str) -> str:
    return app_name.replace('-', '_')


def _secret(app_name: str, name: str, namespace: str, data: Dict[str, str]) -> dict:
    return {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app.kubernetes.io/name': app_name,
                'app.kubernetes.io/managed-by': 'thinkube-control',
            },
            # ArgoCD does not track the Secret; the annotation keeps it from
            # being reported as extra.
            'annotations': {'argocd.argoproj.io/compare-options': 'IgnoreExtraneous'},
        },
        'type': 'Opaque',
        'stringData': dict(data),
    }


def platform_secrets(app_name: str, namespace: str, values: PlatformValues, *,
                     has_database: bool, has_workflows: bool) -> List[dict]:
    """Every Secret the application's manifests name, in the namespace each belongs to."""
    secrets = [
        _secret(app_name, mlflow_secret_name(app_name), namespace, {
            'keycloak-token-url': values.mlflow_keycloak_token_url,
            'keycloak-client-id': values.mlflow_keycloak_client_id,
            'mlflow-client-secret': values.mlflow_client_secret,
            'mlflow-username': values.mlflow_username,
            'admin-password': values.mlflow_password,
            'seaweedfs-password': values.seaweedfs_secret_key,
        }),
        _secret(app_name, storage_secret_name(app_name), namespace, {
            'endpoint': values.seaweedfs_endpoint,
            'access-key': values.seaweedfs_access_key,
            'secret-key': values.seaweedfs_secret_key,
        }),
        _secret(app_name, build_secret_name(app_name), BUILD_NAMESPACE, {
            'username': values.admin_username,
            'password': values.admin_password,
        }),
    ]
    if has_database:
        db = database_name(app_name)
        host = 'postgresql-official.postgres.svc.cluster.local'
        secrets.append(_secret(app_name, db_secret_name(app_name), namespace, {
            'url': f'postgresql://{values.admin_username}:{values.admin_password}@{host}:5432/{db}',
            'username': values.admin_username,
            'password': values.admin_password,
            'database': db,
            'host': host,
            'port': '5432',
        }))
    if has_workflows:
        secrets.append(_secret(app_name, ARTIFACT_SECRET_NAME, namespace, {
            'accesskey': values.seaweedfs_access_key,
            'secretkey': values.seaweedfs_secret_key,
        }))
    return secrets
