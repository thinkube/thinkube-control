# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Who answers at an application's address.

An application is served at <name>.<domain>. A platform service may already
be there (storage.<domain> is the SeaweedFS UI, notebooks.<domain> is
JupyterHub); a second route for the same host would take part of its paths.
"""

from typing import Any, Dict, Iterable, Optional

from kubernetes import client, config


def other_claim(routes: Iterable[Dict[str, Any]], name: str, namespace: str, extension: bool,
                domain: str) -> Optional[str]:
    """The HTTPRoute, as "<namespace>/<name>", that serves <name>.<domain> and
    is not the application's own, or None.

    An app's own routes are those in its namespace; an extension shares
    Control's namespace, so its own carry its app.kubernetes.io/name label.
    """
    host = f"{name}.{domain}"
    for route in routes:
        metadata = route.get("metadata") or {}
        if host not in ((route.get("spec") or {}).get("hostnames") or []):
            continue
        own = metadata.get("namespace") == namespace and (
            not extension or (metadata.get("labels") or {}).get("app.kubernetes.io/name") == name
        )
        if not own:
            return f"{metadata.get('namespace')}/{metadata.get('name')}"
    return None


def cluster_routes() -> Iterable[Dict[str, Any]]:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi().list_cluster_custom_object(
        "gateway.networking.k8s.io", "v1", "httproutes"
    ).get("items", [])
