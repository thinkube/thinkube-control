# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Which manifests an application's thinkube.yaml calls for.

The initial deploy and a later regeneration write the same k8s/ directory
from the same templates. Two copies of the list drift; this is the one copy
both read, and it needs no cluster to answer.
"""

from typing import Any, Dict, List

import yaml

BASE_RESOURCES = [
    'namespace.yaml',
    'resource-policies.yaml',
    'app-metadata.yaml',
    # The discovery ConfigMap (service_config.py)
    'service-config.yaml',
]

# An extension is a part of Thinkube Control: it runs in Control's namespace,
# which Control owns with its limits and quota, so an extension's manifests
# carry no Namespace, LimitRange or ResourceQuota, and its Services carry its
# name, as its Deployments do.
EXTENSION_NAMESPACE = 'thinkube-control'
# Names an extension cannot take: Thinkube Control's own objects in its
# namespace carry them, and tools that find a workload by part of its name
# would reach Control's (thinkube-control-backend, node-metrics, ...).
RESERVED_EXTENSION_NAMES = frozenset({
    'thinkube', 'thinkube-control', 'control', 'backend', 'frontend',
    'llm', 'proxy', 'llm-proxy', 'node', 'metrics', 'node-metrics',
})
NAMESPACE_RESOURCES = ('namespace.yaml', 'resource-policies.yaml')


def is_extension(config: Dict[str, Any]) -> bool:
    return (config.get('spec', {}).get('deployment') or {}).get('type') == 'extension'


def service_name(app_name: str, container_name: str, extension: bool) -> str:
    """The Service of a container: its own name in the app's namespace, the
    app's name and its own in Control's namespace, which several share."""
    return f'{app_name}-{container_name}' if extension else container_name


def artifact_repository_name(app_name: str, extension: bool) -> str:
    """The ConfigMap of the app's Argo artifact repository. In an app's own
    namespace it is the one Argo reads when a workflow names none; in Control's
    namespace each extension has its own, which its workflows name."""
    return f'{app_name}-artifact-repositories' if extension else 'artifact-repositories'


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
    needs_storage: bool,
    has_workflows: bool,
    extension: bool,
) -> List[str]:
    """The resources kustomization.yaml lists, in the order it lists them."""
    resources = [r for r in BASE_RESOURCES if not (extension and r in NAMESPACE_RESOURCES)]
    resources.extend(KNATIVE_RESOURCES if is_knative else APP_RESOURCES)
    if needs_storage:
        resources.append('storage-pvc.yaml')
    if has_workflows:
        resources.append('workflows.yaml')
    resources.append(POSTSYNC_HOOK)
    return resources


def image_variable(container_name: str) -> str:
    """The environment variable that names a container's image to a workflow step."""
    return 'CONTAINER_IMAGE_' + container_name.upper().replace('-', '_')


def kustomization_replacements(app_name: str, containers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One replacement per container: the image its deployment finally runs, copied into the
    container's CONTAINER_IMAGE_<NAME> variable in every deployment of the application.

    Kustomize applies replacements after the images transformer, so the value
    carries the build's pinned tag rather than the `latest` the template writes.
    A step therefore runs the same build as the application that submitted it.
    """
    names = [c['name'] for c in containers]
    replacements = []
    for source in names:
        replacements.append({
            'source': {
                'kind': 'Deployment',
                'name': f'{app_name}-{source}',
                'fieldPath': f'spec.template.spec.containers.[name={source}].image',
            },
            'targets': [
                {
                    'select': {'kind': 'Deployment', 'name': f'{app_name}-{target}'},
                    'fieldPaths': [
                        f'spec.template.spec.containers.[name={target}].env.[name={image_variable(source)}].value'
                    ],
                }
                for target in names
            ],
        })
    return replacements


def kustomization_content(
    *,
    app_name: str,
    container_registry: str,
    containers: List[Dict[str, Any]],
    resources: List[str],
    has_workflows: bool,
) -> str:
    """The kustomization.yaml of an application, as text."""
    document: Dict[str, Any] = {
        'apiVersion': 'kustomize.config.k8s.io/v1beta1',
        'kind': 'Kustomization',
        'resources': resources,
        'images': [
            {'name': f'{container_registry}/thinkube/{app_name}-{c["name"]}', 'newTag': 'latest'} for c in containers
        ],
    }
    if has_workflows:
        document['replacements'] = kustomization_replacements(app_name, containers)
    return yaml.dump(document, sort_keys=False, default_flow_style=False)
