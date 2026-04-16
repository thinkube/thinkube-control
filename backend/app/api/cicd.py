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
ARGOCD_NAMESPACE = "argocd"


def _get_k8s_clients():
    """Get Kubernetes API clients (lazy init, in-cluster or kubeconfig)."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi(), client.CoreV1Api(), client.AppsV1Api()


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
        custom_api, _, _ = _get_k8s_clients()
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
        custom_api, _, _ = _get_k8s_clients()

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


def _get_argocd_deploy_stages(
    custom_api, core_v1, apps_v1, app_name: str, target_namespace: str, after_epoch: Optional[float]
) -> list:
    """Query ArgoCD Application and its Deployment rollout to build deploy stages.

    Returns a list of stage dicts for each Deployment managed by the ArgoCD app,
    with status reflecting the rollout health and timing from the last sync.
    """
    stages = []
    try:
        argocd_app = custom_api.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGOCD_NAMESPACE,
            plural="applications",
            name=app_name,
        )
    except ApiException:
        return stages

    op_state = argocd_app.get("status", {}).get("operationState", {})
    sync_started = _parse_iso_to_epoch(op_state.get("startedAt"))
    sync_finished = _parse_iso_to_epoch(op_state.get("finishedAt"))
    sync_phase = op_state.get("phase", "")

    # Only include deploy stages that are related to this build
    # (sync must have started after the workflow started)
    if after_epoch and sync_started and sync_started < after_epoch:
        return stages

    # Map ArgoCD sync phase to our status
    argocd_status_map = {
        "Succeeded": "SUCCEEDED",
        "Running": "RUNNING",
        "Failed": "FAILED",
        "Error": "FAILED",
    }

    # Add a stage for the ArgoCD sync itself
    sync_duration = None
    if sync_started and sync_finished:
        sync_duration = sync_finished - sync_started

    stages.append({
        "id": f"argocd-sync-{app_name}",
        "stageName": "argocd-sync",
        "component": "argocd",
        "status": argocd_status_map.get(sync_phase, "PENDING"),
        "startedAt": sync_started,
        "completedAt": sync_finished,
        "duration": sync_duration,
        "errorMessage": op_state.get("message") if sync_phase in ("Failed", "Error") else None,
        "podName": None,
        "details": {"type": "argocd"},
    })

    # Add a stage for each Deployment rollout in the target namespace
    if not target_namespace:
        return stages

    try:
        deployments = apps_v1.list_namespaced_deployment(
            namespace=target_namespace,
            label_selector=f"app.kubernetes.io/instance={app_name}",
        )
        # Fallback: if no label match, try getting deployments from ArgoCD resources
        if not deployments.items:
            resources = argocd_app.get("status", {}).get("resources", [])
            deploy_names = [
                r["name"] for r in resources
                if r.get("kind") == "Deployment" and r.get("group", "") == "apps"
            ]
            for dname in deploy_names:
                try:
                    dep = apps_v1.read_namespaced_deployment(name=dname, namespace=target_namespace)
                    deployments.items.append(dep)
                except ApiException:
                    pass
    except ApiException:
        return stages

    for dep in deployments.items:
        dep_name = dep.metadata.name
        dep_status = dep.status

        # Determine rollout status from conditions
        rollout_status = "RUNNING"
        error_msg = None
        rollout_finished = None

        ready = dep_status.ready_replicas or 0
        desired = dep.spec.replicas or 1
        updated = dep_status.updated_replicas or 0

        if updated >= desired and ready >= desired:
            rollout_status = "SUCCEEDED"
        elif dep_status.conditions:
            for cond in dep_status.conditions:
                if cond.type == "Progressing" and cond.status == "False":
                    rollout_status = "FAILED"
                    error_msg = cond.message
                    break

        # Use the newest pod's start time as rollout start, ready time as end
        rollout_started = sync_finished  # rollout starts after sync
        if rollout_status == "SUCCEEDED" and dep_status.conditions:
            for cond in dep_status.conditions:
                if cond.type == "Available" and cond.status == "True" and cond.last_transition_time:
                    rollout_finished = cond.last_transition_time.timestamp()

        rollout_duration = None
        if rollout_started and rollout_finished:
            rollout_duration = rollout_finished - rollout_started

        # Find the current pod name for log access
        pod_name = None
        try:
            pods = core_v1.list_namespaced_pod(
                namespace=target_namespace,
                label_selector=f"app={dep_name}",
                limit=1,
            )
            if pods.items:
                pod_name = pods.items[0].metadata.name
        except ApiException:
            pass

        stages.append({
            "id": f"deploy-{dep_name}",
            "stageName": f"deploy-{dep_name.split('-', 2)[-1] if '-' in dep_name else dep_name}",
            "component": "deployment",
            "status": rollout_status,
            "startedAt": rollout_started,
            "completedAt": rollout_finished,
            "duration": rollout_duration,
            "errorMessage": error_msg,
            "podName": pod_name,
            "details": {"type": "deployment", "namespace": target_namespace},
        })

    return stages


@router.get("/pipelines/{workflow_name}")
async def get_pipeline(
    workflow_name: str,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed pipeline info for a specific Argo Workflow, including stages.

    Enriches build stages with ArgoCD deployment stages when the workflow
    has ``thinkube.io/app-name`` and ``thinkube.io/namespace`` labels.
    """
    try:
        custom_api, core_v1, apps_v1 = _get_k8s_clients()

        wf = custom_api.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGO_NAMESPACE,
            plural="workflows",
            name=workflow_name,
        )

        pipeline = _workflow_to_pipeline(wf)

        # Enrich with ArgoCD deploy stages
        app_name = pipeline.get("appName")
        target_ns = pipeline.get("namespace")
        if app_name:
            deploy_stages = _get_argocd_deploy_stages(
                custom_api, core_v1, apps_v1,
                app_name, target_ns, pipeline.get("startedAt"),
            )
            pipeline["stages"].extend(deploy_stages)
            pipeline["stageCount"] = len(pipeline["stages"])

        return pipeline

    except ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail="Workflow not found")
        logger.error(f"K8s API error getting workflow: {e}")
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {e.reason}")
    except Exception as e:
        logger.error(f"Error getting pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _resolve_pod_name(core_v1, pod_name_or_node_id: str, workflow_name: str) -> str:
    """Resolve a pod name from either a direct pod name or an Argo node ID.

    For single-step workflows the node ID equals the pod name.  For DAG
    workflows the node ID (e.g. ``wf-abc-123``) differs from the actual pod
    name (e.g. ``wf-abc-step-name-123``).  When a direct lookup fails we
    search for the pod by its ``workflows.argoproj.io/node-id`` annotation.
    """
    # Try direct lookup first (works for single-step workflows)
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name_or_node_id, namespace=ARGO_NAMESPACE)
        pod_labels = pod.metadata.labels or {}
        if pod_labels.get("workflows.argoproj.io/workflow") == workflow_name:
            return pod_name_or_node_id
    except ApiException as e:
        if e.status != 404:
            raise

    # Fallback: find pod by node-id annotation within the workflow
    pods = core_v1.list_namespaced_pod(
        namespace=ARGO_NAMESPACE,
        label_selector=f"workflows.argoproj.io/workflow={workflow_name}",
    )
    for p in pods.items:
        annotations = p.metadata.annotations or {}
        if annotations.get("workflows.argoproj.io/node-id") == pod_name_or_node_id:
            return p.metadata.name

    raise HTTPException(
        status_code=404,
        detail=f"Pod for node {pod_name_or_node_id} not found. It may have been garbage collected.",
    )


@router.get("/pipelines/{workflow_name}/logs/{pod_name}")
async def get_step_logs(
    workflow_name: str,
    pod_name: str,
    namespace: Optional[str] = Query(None, description="Pod namespace (defaults to argo)"),
    container: Optional[str] = Query(None, description="Container name (defaults to main for argo, auto-detected for deployments)"),
    tail_lines: Optional[int] = Query(500, ge=1, le=10000, description="Number of log lines"),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get logs for a specific workflow step or deployment pod.

    The *pod_name* parameter may be a literal Kubernetes pod name or an Argo
    Workflow node ID — the endpoint resolves the actual pod automatically.
    For deployment pods, pass the ``namespace`` query parameter.
    """
    try:
        _, core_v1, _ = _get_k8s_clients()

        target_namespace = namespace or ARGO_NAMESPACE
        actual_pod_name = pod_name
        target_container = container

        if target_namespace == ARGO_NAMESPACE:
            # Argo build pod — resolve node-id to pod name
            actual_pod_name = _resolve_pod_name(core_v1, pod_name, workflow_name)
            if not target_container:
                target_container = "main"
        else:
            # Deployment pod — verify it exists; auto-detect first container
            try:
                pod = core_v1.read_namespaced_pod(name=pod_name, namespace=target_namespace)
                if not target_container and pod.spec.containers:
                    target_container = pod.spec.containers[0].name
            except ApiException as e:
                if e.status == 404:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Pod {pod_name} not found in namespace {target_namespace}.",
                    )
                raise

        logs = core_v1.read_namespaced_pod_log(
            name=actual_pod_name,
            namespace=target_namespace,
            container=target_container,
            tail_lines=tail_lines,
        )

        return {
            "workflowName": workflow_name,
            "podName": actual_pod_name,
            "namespace": target_namespace,
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
