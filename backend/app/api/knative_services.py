"""
Knative Services API endpoints.
Provides status, scaling info, and management for Knative services
deployed via thinkube-control.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException

from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knative-services"])


class KnativeServiceInfo(BaseModel):
    """Knative service status information"""

    name: str
    namespace: str
    url: Optional[str] = None
    status: str  # Ready, NotReady, Unknown
    ready_condition: Optional[str] = None
    latest_revision: Optional[str] = None
    min_scale: int = 0
    max_scale: int = 5
    container_concurrency: int = 0
    timeout_seconds: int = 300
    current_replicas: int = 0
    image: Optional[str] = None
    created_at: Optional[str] = None
    last_transition: Optional[str] = None


class KnativeServiceListResponse(BaseModel):
    """Response for listing Knative services"""

    services: List[KnativeServiceInfo]
    total_count: int


async def _get_k8s_custom_client():
    """Get an async Kubernetes custom objects client."""
    await config.load_kube_config()
    return client.CustomObjectsApi()


def _parse_knative_service(ksvc: dict) -> KnativeServiceInfo:
    """Parse a Knative Service resource into a KnativeServiceInfo."""
    metadata = ksvc.get("metadata", {})
    spec = ksvc.get("spec", {})
    status = ksvc.get("status", {})
    template_spec = spec.get("template", {})
    template_meta = template_spec.get("metadata", {})
    annotations = template_meta.get("annotations", {})
    container_spec = template_spec.get("spec", {})

    # Get scaling annotations
    min_scale = int(annotations.get("autoscaling.knative.dev/min-scale", "0"))
    max_scale = int(annotations.get("autoscaling.knative.dev/max-scale", "5"))

    # Get container concurrency and timeout from spec
    container_concurrency = container_spec.get("containerConcurrency", 0)
    timeout_seconds = container_spec.get("timeoutSeconds", 300)

    # Get container image
    containers = container_spec.get("containers", [])
    image = containers[0].get("image", "") if containers else ""

    # Parse status conditions
    conditions = status.get("conditions", [])
    ready_status = "Unknown"
    ready_message = None
    last_transition = None

    for condition in conditions:
        if condition.get("type") == "Ready":
            cond_status = condition.get("status", "Unknown")
            ready_status = "Ready" if cond_status == "True" else "NotReady"
            ready_message = condition.get("message")
            last_transition = condition.get("lastTransitionTime")
            break

    # Get URL
    url = status.get("url") or status.get("address", {}).get("url")

    return KnativeServiceInfo(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        url=url,
        status=ready_status,
        ready_condition=ready_message,
        latest_revision=status.get("latestReadyRevisionName"),
        min_scale=min_scale,
        max_scale=max_scale,
        container_concurrency=container_concurrency,
        timeout_seconds=timeout_seconds,
        current_replicas=0,  # Will be enriched from pods
        image=image,
        created_at=metadata.get("creationTimestamp"),
        last_transition=last_transition,
    )


@router.get(
    "/knative-services",
    response_model=KnativeServiceListResponse,
    operation_id="list_knative_services",
)
async def list_knative_services(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    List all Knative services across all namespaces.

    Returns service status, scaling configuration, and current replica count.
    """
    try:
        k8s_custom = await _get_k8s_custom_client()

        try:
            result = await k8s_custom.list_cluster_custom_object(
                group="serving.knative.dev",
                version="v1",
                plural="services",
            )
        finally:
            await k8s_custom.api_client.close()

        services = []
        for item in result.get("items", []):
            # Skip test/system namespaces
            ns = item.get("metadata", {}).get("namespace", "")
            if ns in ("kn", "knative-serving", "knative-eventing"):
                continue

            info = _parse_knative_service(item)
            services.append(info)

        # Enrich with current replica counts from pods
        try:
            k8s_core = client.CoreV1Api()
            try:
                for svc in services:
                    pods = await k8s_core.list_namespaced_pod(
                        namespace=svc.namespace,
                        label_selector=f"serving.knative.dev/service={svc.name}",
                    )
                    running = sum(
                        1
                        for p in pods.items
                        if p.status and p.status.phase == "Running"
                    )
                    svc.current_replicas = running
            finally:
                await k8s_core.api_client.close()
        except Exception as e:
            logger.warning(f"Failed to get pod counts: {e}")

        return KnativeServiceListResponse(
            services=services,
            total_count=len(services),
        )

    except Exception as e:
        logger.error(f"Failed to list Knative services: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list Knative services: {str(e)}",
        )


@router.get(
    "/knative-services/{namespace}/{name}",
    response_model=KnativeServiceInfo,
    operation_id="get_knative_service",
)
async def get_knative_service(
    namespace: str,
    name: str,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed status of a single Knative service."""
    try:
        k8s_custom = await _get_k8s_custom_client()

        try:
            result = await k8s_custom.get_namespaced_custom_object(
                group="serving.knative.dev",
                version="v1",
                namespace=namespace,
                plural="services",
                name=name,
            )
        finally:
            await k8s_custom.api_client.close()

        info = _parse_knative_service(result)

        # Get pod count
        try:
            k8s_core = client.CoreV1Api()
            try:
                pods = await k8s_core.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"serving.knative.dev/service={name}",
                )
                info.current_replicas = sum(
                    1
                    for p in pods.items
                    if p.status and p.status.phase == "Running"
                )
            finally:
                await k8s_core.api_client.close()
        except Exception:
            pass

        return info

    except ApiException as e:
        if e.status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Knative service '{name}' not found in namespace '{namespace}'",
            )
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get Knative service: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Knative service: {str(e)}",
        )


@router.delete(
    "/knative-services/{namespace}/{name}",
    operation_id="delete_knative_service",
)
async def delete_knative_service(
    namespace: str,
    name: str,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Delete a Knative service."""
    try:
        k8s_custom = await _get_k8s_custom_client()

        try:
            await k8s_custom.delete_namespaced_custom_object(
                group="serving.knative.dev",
                version="v1",
                namespace=namespace,
                plural="services",
                name=name,
            )
        finally:
            await k8s_custom.api_client.close()

        return {"message": f"Knative service '{name}' deleted from namespace '{namespace}'"}

    except ApiException as e:
        if e.status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Knative service '{name}' not found in namespace '{namespace}'",
            )
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete Knative service: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete Knative service: {str(e)}",
        )
