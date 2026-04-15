# app/api/cicd.py
"""CI/CD monitoring endpoints - queries Argo Workflows directly from Kubernetes."""

from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Depends
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import logging

from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)

router = APIRouter()

# Status mapping from Argo phase to extension-compatible status
PHASE_TO_STATUS = {
    "Pending": "PENDING",
    "Running": "RUNNING",
    "Succeeded": "SUCCEEDED",
    "Failed": "FAILED",
    "Error": "FAILED",
    "Skipped": "SKIPPED",
}

ARGO_NAMESPACE = "argo"


def _get_k8s_clients():
    """Get Kubernetes API clients (lazy init, in-cluster or kubeconfig)."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi(), client.CoreV1Api()


def _parse_iso_to_epoch(iso_str: Optional[str]) -> Optional[float]:
    """Convert ISO8601 timestamp to Unix epoch seconds."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _workflow_to_pipeline(wf: dict) -> dict:
    """Map an Argo Workflow object to the Pipeline interface the extension expects."""
    metadata = wf.get("metadata", {})
    status = wf.get("status", {})
    labels = metadata.get("labels", {})
    spec = wf.get("spec", {})
    params = {
        p["name"]: p.get("value", "")
        for p in spec.get("arguments", {}).get("parameters", [])
    }

    started_at = _parse_iso_to_epoch(status.get("startedAt"))
    finished_at = _parse_iso_to_epoch(status.get("finishedAt"))
    duration = None
    if started_at and finished_at:
        duration = finished_at - started_at

    # Extract stages from status.nodes (only Pod-type nodes = actual work)
    nodes = status.get("nodes", {})
    stages = []
    for node_id, node in nodes.items():
        if node.get("type") != "Pod":
            continue
        node_started = _parse_iso_to_epoch(node.get("startedAt"))
        node_finished = _parse_iso_to_epoch(node.get("finishedAt"))
        node_duration = None
        if node_started and node_finished:
            node_duration = node_finished - node_started

        stages.append({
            "id": node_id,
            "stageName": node.get("displayName", node_id),
            "component": node.get("templateName", "unknown"),
            "status": PHASE_TO_STATUS.get(node.get("phase", ""), "PENDING"),
            "startedAt": node_started,
            "completedAt": node_finished,
            "duration": node_duration,
            "errorMessage": node.get("message"),
            "podName": node_id,
            "details": {},
        })

    # Sort stages by start time
    stages.sort(key=lambda s: s.get("startedAt") or 0)

    return {
        "id": metadata.get("name"),
        "appName": labels.get("thinkube.io/app-name", metadata.get("name", "")),
        "namespace": labels.get("thinkube.io/namespace", ""),
        "status": PHASE_TO_STATUS.get(status.get("phase", ""), "PENDING"),
        "startedAt": started_at,
        "completedAt": finished_at,
        "duration": duration,
        "stages": stages,
        "stageCount": len(stages),
        "triggerType": "webhook",
        "triggerUser": None,
        "branch": None,
        "commitSha": params.get("image_tag"),
        "commitMessage": None,
    }


@router.get("/health")
async def cicd_health():
    """Health check for CI/CD API."""
    try:
        custom_api, _ = _get_k8s_clients()
        # Quick check: list 1 workflow
        custom_api.list_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGO_NAMESPACE,
            plural="workflows",
            limit=1,
        )
        return {"status": "healthy", "source": "kubernetes"}
    except Exception as e:
        logger.error(f"CI/CD health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}


@router.get("/pipelines")
async def list_pipelines(
    app_name: Optional[str] = Query(None, description="Filter by app name"),
    status: Optional[str] = Query(None, description="Filter by status (Succeeded, Failed, Running)"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List CI/CD build workflows from Argo.

    Filters out webhook-build trigger workflows by requiring the
    thinkube.io/app-name label (only real build workflows have it).
    """
    try:
        custom_api, _ = _get_k8s_clients()

        # Build label selector - thinkube.io/app-name excludes webhook-build triggers
        selectors = ["thinkube.io/app-name"]
        if app_name:
            selectors = [f"thinkube.io/app-name={app_name}"]
        if status:
            selectors.append(f"workflows.argoproj.io/phase={status}")

        label_selector = ",".join(selectors)

        result = custom_api.list_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGO_NAMESPACE,
            plural="workflows",
            label_selector=label_selector,
        )

        workflows = result.get("items", [])

        # Sort by creation timestamp descending (most recent first)
        workflows.sort(
            key=lambda w: w.get("metadata", {}).get("creationTimestamp", ""),
            reverse=True,
        )

        # Apply limit
        workflows = workflows[:limit]

        # Map to pipeline format (lightweight - skip full node details for list)
        pipelines = []
        for wf in workflows:
            p = _workflow_to_pipeline(wf)
            # For list view, don't include full stage details to keep response small
            p["stages"] = []
            pipelines.append(p)

        return {
            "pipelines": pipelines,
            "total": len(pipelines),
            "limit": limit,
            "offset": 0,
        }

    except ApiException as e:
        logger.error(f"K8s API error listing workflows: {e}")
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {e.reason}")
    except Exception as e:
        logger.error(f"Error listing pipelines: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipelines/{workflow_name}")
async def get_pipeline(
    workflow_name: str,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed pipeline info for a specific Argo Workflow, including stages."""
    try:
        custom_api, _ = _get_k8s_clients()

        wf = custom_api.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGO_NAMESPACE,
            plural="workflows",
            name=workflow_name,
        )

        return _workflow_to_pipeline(wf)

    except ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail="Workflow not found")
        logger.error(f"K8s API error getting workflow: {e}")
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {e.reason}")
    except Exception as e:
        logger.error(f"Error getting pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipelines/{workflow_name}/logs/{pod_name}")
async def get_step_logs(
    workflow_name: str,
    pod_name: str,
    tail_lines: Optional[int] = Query(500, ge=1, le=10000, description="Number of log lines"),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get logs for a specific workflow step (pod).

    Returns the build/test output for the given step, which is especially
    useful for diagnosing failed builds.
    """
    try:
        _, core_v1 = _get_k8s_clients()

        # Verify the pod belongs to the requested workflow
        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=ARGO_NAMESPACE)
        except ApiException as e:
            if e.status == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"Pod {pod_name} not found. It may have been garbage collected.",
                )
            raise

        pod_labels = pod.metadata.labels or {}
        pod_workflow = pod_labels.get("workflows.argoproj.io/workflow", "")
        if pod_workflow != workflow_name:
            raise HTTPException(
                status_code=400,
                detail=f"Pod {pod_name} does not belong to workflow {workflow_name}",
            )

        # Get logs from the main container
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=ARGO_NAMESPACE,
            container="main",
            tail_lines=tail_lines,
        )

        return {
            "workflowName": workflow_name,
            "podName": pod_name,
            "logs": logs,
            "tailLines": tail_lines,
        }

    except HTTPException:
        raise
    except ApiException as e:
        if e.status == 404:
            raise HTTPException(
                status_code=404,
                detail="Pod not found. Logs may no longer be available.",
            )
        logger.error(f"K8s API error getting logs: {e}")
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {e.reason}")
    except Exception as e:
        logger.error(f"Error getting step logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
