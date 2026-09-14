"""Carry a changed secret value to the applications that receive it.

For each application recorded as using the secret, the value is written into
its <app>-secrets and its workloads are restarted, so the containers read the
new value: Deployments through a new pod template annotation, Knative services
through a new revision. One application failing does not stop the others; each
failure is returned with its reason.
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple

from kubernetes import client
from kubernetes.client.rest import ApiException

from app.services.deploy_identity import MERGE_PATCH

RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"


def _restart_template(stamp: str) -> dict:
    return {"spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: stamp}}}}}


def _knative_services(custom: client.CustomObjectsApi, namespace: str) -> list:
    try:
        return custom.list_namespaced_custom_object(
            group="serving.knative.dev", version="v1", namespace=namespace, plural="services"
        )["items"]
    except ApiException as e:
        # Knative is an optional component. Without it the API has no Knative
        # services resource, so the namespace holds none.
        if e.status == 404:
            return []
        raise


def propagate(
    secret_name: str, value: str, app_names: List[str], api_client: client.ApiClient
) -> Tuple[List[str], Dict[str, str]]:
    """Returns the applications restarted, and the reason for each that was not."""
    core = client.CoreV1Api(api_client)
    apps = client.AppsV1Api(api_client)
    custom = client.CustomObjectsApi(api_client)
    stamp = datetime.now(timezone.utc).isoformat()

    restarted: List[str] = []
    failed: Dict[str, str] = {}
    for app in app_names:
        try:
            core.patch_namespaced_secret(
                f"{app}-secrets", app, {"stringData": {secret_name: value}}, _content_type=MERGE_PATCH
            )
            services = _knative_services(custom, app)
            if services:
                # Knative owns the Deployments of its revisions; a new revision
                # comes from the service's template.
                for service in services:
                    custom.patch_namespaced_custom_object(
                        group="serving.knative.dev", version="v1", namespace=app, plural="services",
                        name=service["metadata"]["name"], body=_restart_template(stamp),
                        _content_type=MERGE_PATCH,
                    )
            else:
                deployments = apps.list_namespaced_deployment(
                    app, label_selector=f"app.kubernetes.io/name={app}"
                ).items
                if not deployments:
                    raise RuntimeError(f"no Deployment labelled app.kubernetes.io/name={app} in namespace {app}")
                for deployment in deployments:
                    apps.patch_namespaced_deployment(
                        deployment.metadata.name, app, _restart_template(stamp), _content_type=MERGE_PATCH
                    )
            restarted.append(app)
        except ApiException as e:
            failed[app] = f"{e.status} {e.reason}"
        except RuntimeError as e:
            failed[app] = str(e)
    return restarted, failed
