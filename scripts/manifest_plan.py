"""Which manifests an application's thinkube.yaml calls for.

The initial deploy and a later regeneration write the same k8s/ directory
from the same templates. Two copies of the list drift; this is the one copy
both read, and it needs no cluster to answer.
"""

from typing import Any, Dict, List

BASE_RESOURCES = [
    'namespace.yaml',
    'resource-policies.yaml',
    'mlflow-secrets.yaml',
    'app-metadata.yaml',
]

KNATIVE_RESOURCES = ['knative-service.yaml']

APP_RESOURCES = ['deployments.yaml', 'services.yaml', 'ingress.yaml']

POSTSYNC_HOOK = 'argocd-postsync-hook.yaml'


def declared_services(config: Dict[str, Any]) -> List[str]:
    return config.get('spec', {}).get('services', []) or []


def has_service(config: Dict[str, Any], name: str) -> bool:
    """Whether a service is declared, ignoring any ":instance" suffix."""
    return any(s.split(':')[0] == name for s in declared_services(config))


def needs_storage(config: Dict[str, Any]) -> bool:
    containers = config.get('spec', {}).get('containers', []) or []
    return (
        has_service(config, 'storage')
        or any((c.get('gpu') or {}).get('count') for c in containers)
        or any('volume' in c for c in containers)
    )


def kustomization_resources(
    *,
    is_knative: bool,
    has_database: bool,
    needs_storage: bool,
    has_workflows: bool,
) -> List[str]:
    """The resources kustomization.yaml lists, in the order it lists them."""
    resources = list(BASE_RESOURCES)
    resources.extend(KNATIVE_RESOURCES if is_knative else APP_RESOURCES)
    if has_database:
        resources.append('postgresql.yaml')
    if needs_storage:
        resources.append('storage-pvc.yaml')
    if has_workflows:
        resources.append('workflows.yaml')
    resources.append(POSTSYNC_HOOK)
    return resources
