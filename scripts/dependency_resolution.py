# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Which running service a thinkube.yaml dependency names, and its cluster URL.

A dependency's `type` is matched against the services the cluster runs: a
Knative service first, whose name or namespace contains the type, then a
Kubernetes Service, preferring one whose name and namespace both contain it.

Both deploy paths use this module with the listings their own Kubernetes
client returns: the first deploy in deploy_application.py and regeneration
in manifest_generator.py.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional

SKIPPED_NAMESPACES = frozenset({'kube-system', 'kube-public', 'default'})


@dataclass(frozen=True)
class KnativeView:
    name: str
    namespace: str
    url: Optional[str]


@dataclass(frozen=True)
class ServiceView:
    name: str
    namespace: str
    first_port: Optional[int]


def knative_url(services: Iterable[KnativeView], dep_type: str) -> Optional[str]:
    """The address of the first Knative service matching the type, or None when none matches."""
    for svc in services:
        if dep_type in svc.name or dep_type in svc.namespace:
            if not svc.url:
                raise RuntimeError(
                    f"Knative service {svc.namespace}/{svc.name} matches dependency type "
                    f"'{dep_type}' but has no address yet: it is not ready."
                )
            return svc.url
    return None


def service_url(services: Iterable[ServiceView], dep_type: str) -> Optional[str]:
    """The cluster URL of the Service matching the type, or None when none matches."""
    candidates: List[ServiceView] = [s for s in services if s.namespace not in SKIPPED_NAMESPACES]
    match = next((s for s in candidates if dep_type in s.name and dep_type in s.namespace), None)
    if match is None:
        match = next((s for s in candidates if dep_type in s.name), None)
    if match is None:
        return None
    if match.first_port is None:
        raise RuntimeError(
            f"Service {match.namespace}/{match.name} matches dependency type '{dep_type}' but exposes no port."
        )
    return f"http://{match.name}.{match.namespace}.svc.cluster.local:{match.first_port}"


def knative_view(item: dict) -> KnativeView:
    """A Knative service object, as the custom objects API returns it."""
    return KnativeView(
        name=item['metadata']['name'],
        namespace=item['metadata']['namespace'],
        url=((item.get('status') or {}).get('address') or {}).get('url'),
    )


def service_view(svc) -> ServiceView:
    """A V1Service from either Kubernetes client."""
    ports = svc.spec.ports or []
    return ServiceView(
        name=svc.metadata.name,
        namespace=svc.metadata.namespace,
        first_port=ports[0].port if ports else None,
    )

