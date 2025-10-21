"""Kubernetes service management functionality"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.models.services import Service as ServiceModel, ServiceAction
from app.models.service_schemas import ServiceType


logger = logging.getLogger(__name__)


class K8sServiceManager:
    """Manage Kubernetes deployments and services"""

    def __init__(self):
        """Initialize Kubernetes client"""
        self._init_kubernetes()

    def _init_kubernetes(self):
        """Initialize Kubernetes client"""
        try:
            # Try in-cluster config first (when running in pod)
            config.load_incluster_config()
        except config.ConfigException:
            try:
                # Fall back to kubeconfig file
                config.load_kube_config()
            except config.ConfigException as e:
                logger.error(f"Failed to initialize Kubernetes client: {e}")
                raise

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def scale_deployment(
        self, namespace: str, name: str, replicas: int
    ) -> Tuple[bool, Optional[str]]:
        """Scale a deployment to specified replicas

        Args:
            namespace: Kubernetes namespace
            name: Deployment name
            replicas: Target replica count

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Find the deployment
            deployments = self.apps_v1.list_namespaced_deployment(namespace)
            deployment = None

            for d in deployments.items:
                if d.metadata.name == name or name in d.metadata.name:
                    deployment = d
                    break

            if not deployment:
                return (
                    False,
                    f"Deployment not found for service {name} in namespace {namespace}",
                )

            # Scale the deployment
            deployment.spec.replicas = replicas
            self.apps_v1.patch_namespaced_deployment(
                name=deployment.metadata.name, namespace=namespace, body=deployment
            )

            logger.info(
                f"Scaled deployment {deployment.metadata.name} in namespace {namespace} to {replicas} replicas"
            )
            return True, None

        except ApiException as e:
            error_msg = f"Failed to scale deployment: {e.reason}"
            logger.error(error_msg)
            return False, error_msg

    def get_namespace_gpu_usage(self, namespace: str) -> Optional[Dict[str, Any]]:
        """Get GPU usage for all pods in a namespace

        Args:
            namespace: Kubernetes namespace

        Returns:
            Dictionary with total GPU usage or None if no GPUs used
        """
        try:
            # Get ALL pods in the namespace, not just from deployments
            # This includes JupyterHub user pods, standalone pods, etc.
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)

            total_gpus = 0
            gpu_nodes = set()

            # Check each pod for GPU resources
            for pod in pods.items:
                # Only count pods that are actually running or pending
                # Skip pods in Failed, Unknown, Succeeded, or Terminating states
                pod_phase = pod.status.phase if pod.status else None
                if pod_phase not in ['Running', 'Pending']:
                    logger.debug(f"Skipping pod {pod.metadata.name} with phase {pod_phase}")
                    continue

                # Also check for ContainerStatusUnknown
                if pod.status and pod.status.container_statuses:
                    has_unknown = any(
                        cs.state.waiting and cs.state.waiting.reason == 'ContainerStatusUnknown'
                        for cs in pod.status.container_statuses
                        if cs.state and cs.state.waiting
                    )
                    if has_unknown:
                        logger.debug(f"Skipping pod {pod.metadata.name} with ContainerStatusUnknown")
                        continue

                for container in pod.spec.containers:
                    if container.resources:
                        # Check limits (what's actually allocated)
                        if container.resources.limits:
                            gpu_count = container.resources.limits.get("nvidia.com/gpu", 0)
                            if gpu_count:
                                total_gpus += int(gpu_count)
                                if pod.spec.node_name:
                                    gpu_nodes.add(pod.spec.node_name)
            
            if total_gpus > 0:
                return {
                    "total_gpus": total_gpus,
                    "gpu_nodes": list(gpu_nodes)
                }
            
            return None
            
        except ApiException as e:
            logger.error(f"Failed to get namespace GPU usage: {e}")
            return None
    
    def get_deployment_status(
        self, namespace: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """Get current deployment status and resource usage

        Args:
            namespace: Kubernetes namespace
            name: Service/deployment name

        Returns:
            Dictionary with deployment status or None if not found
        """
        try:
            # Try to find a Deployment first
            deployments = self.apps_v1.list_namespaced_deployment(namespace)
            deployment = None

            for d in deployments.items:
                if d.metadata.name == name or name in d.metadata.name:
                    deployment = d
                    break

            if deployment:
                return self._get_deployment_info(deployment, namespace)

            # Try StatefulSet
            statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace)
            for sts in statefulsets.items:
                if sts.metadata.name == name or name in sts.metadata.name:
                    return self._get_statefulset_info(sts, namespace)

            # Try DaemonSet
            daemonsets = self.apps_v1.list_namespaced_daemon_set(namespace)
            for ds in daemonsets.items:
                if ds.metadata.name == name or name in ds.metadata.name:
                    return self._get_daemonset_info(ds, namespace)

            # If no workload found, try to find pods by label
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            matching_pods = []
            for pod in pods.items:
                labels = pod.metadata.labels or {}
                # Check common labels
                if (labels.get("app") == name or
                    labels.get("app.kubernetes.io/name") == name or
                    labels.get("app.kubernetes.io/instance") == name or
                    name in pod.metadata.name):
                    matching_pods.append(pod)

            if matching_pods:
                return self._get_pods_only_info(matching_pods, namespace, name)


        except ApiException as e:
            logger.error(f"Failed to get deployment status: {e}")
            return None

    def get_pods_for_deployment(
        self, namespace: str, deployment_name: str
    ) -> List[Dict[str, Any]]:
        """Get pods for a deployment

        Args:
            namespace: Kubernetes namespace
            deployment_name: Deployment name

        Returns:
            List of pod information
        """
        pods_info = []

        try:
            # Get deployment to find selector labels
            deployment = self.apps_v1.read_namespaced_deployment(
                deployment_name, namespace
            )
            labels = deployment.spec.selector.match_labels

            # Build label selector
            label_selector = ",".join([f"{k}={v}" for k, v in labels.items()])

            # Get pods with matching labels
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            )

            for pod in pods.items:
                pod_info = {
                    "name": pod.metadata.name,
                    "status": pod.status.phase,
                    "ready": all(c.ready for c in pod.status.container_statuses or []),
                    "restart_count": sum(
                        c.restart_count for c in pod.status.container_statuses or []
                    ),
                    "created_at": pod.metadata.creation_timestamp,
                    "node": pod.spec.node_name,
                    "ip": pod.status.pod_ip,
                    "containers": [],
                }

                # Get container information
                for container in pod.spec.containers:
                    container_info = {
                        "name": container.name,
                        "image": container.image,
                        "resources": {},
                    }

                    # Get resource requests/limits
                    if container.resources:
                        if container.resources.requests:
                            container_info["resources"]["cpu_request"] = (
                                container.resources.requests.get("cpu", "0")
                            )
                            container_info["resources"]["memory_request"] = (
                                container.resources.requests.get("memory", "0")
                            )
                            # Check for GPU requests
                            container_info["resources"]["gpu_request"] = (
                                container.resources.requests.get("nvidia.com/gpu", "0")
                            )
                        if container.resources.limits:
                            container_info["resources"]["cpu_limit"] = (
                                container.resources.limits.get("cpu", "0")
                            )
                            container_info["resources"]["memory_limit"] = (
                                container.resources.limits.get("memory", "0")
                            )
                            # Check for GPU limits
                            container_info["resources"]["gpu_limit"] = (
                                container.resources.limits.get("nvidia.com/gpu", "0")
                            )

                    # Get container status
                    for status in pod.status.container_statuses or []:
                        if status.name == container.name:
                            container_info["ready"] = status.ready
                            container_info["restart_count"] = status.restart_count
                            if status.state:
                                if status.state.running:
                                    container_info["state"] = "running"
                                    container_info["started_at"] = (
                                        status.state.running.started_at
                                    )
                                elif status.state.waiting:
                                    container_info["state"] = "waiting"
                                    container_info["waiting_reason"] = (
                                        status.state.waiting.reason
                                    )
                                elif status.state.terminated:
                                    container_info["state"] = "terminated"
                                    container_info["exit_code"] = (
                                        status.state.terminated.exit_code
                                    )
                            break

                    pod_info["containers"].append(container_info)

                pods_info.append(pod_info)

        except ApiException as e:
            logger.error(f"Failed to get pods for deployment: {e}")

        return pods_info

    def restart_deployment(
        self, namespace: str, name: str
    ) -> Tuple[bool, Optional[str]]:
        """Restart a workload (Deployment, StatefulSet, or DaemonSet) by updating an annotation

        Args:
            namespace: Kubernetes namespace
            name: Service/workload name

        Returns:
            Tuple of (success, error_message)
        """
        try:
            restart_time = datetime.utcnow().isoformat()

            # Try Deployment first
            try:
                deployments = self.apps_v1.list_namespaced_deployment(namespace)
                for d in deployments.items:
                    if d.metadata.name == name or name in d.metadata.name:
                        # Update annotation to trigger restart
                        if not d.spec.template.metadata.annotations:
                            d.spec.template.metadata.annotations = {}
                        d.spec.template.metadata.annotations[
                            "kubectl.kubernetes.io/restartedAt"
                        ] = restart_time

                        self.apps_v1.patch_namespaced_deployment(
                            name=d.metadata.name, namespace=namespace, body=d
                        )
                        logger.info(
                            f"Restarted deployment {d.metadata.name} in namespace {namespace}"
                        )
                        return True, None
            except ApiException:
                pass  # Not a deployment, try other types

            # Try StatefulSet
            try:
                statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace)
                for s in statefulsets.items:
                    if s.metadata.name == name or name in s.metadata.name:
                        # Update annotation to trigger restart
                        if not s.spec.template.metadata.annotations:
                            s.spec.template.metadata.annotations = {}
                        s.spec.template.metadata.annotations[
                            "kubectl.kubernetes.io/restartedAt"
                        ] = restart_time

                        self.apps_v1.patch_namespaced_stateful_set(
                            name=s.metadata.name, namespace=namespace, body=s
                        )
                        logger.info(
                            f"Restarted statefulset {s.metadata.name} in namespace {namespace}"
                        )
                        return True, None
            except ApiException:
                pass  # Not a statefulset, try other types

            # Try DaemonSet
            try:
                daemonsets = self.apps_v1.list_namespaced_daemon_set(namespace)
                for d in daemonsets.items:
                    if d.metadata.name == name or name in d.metadata.name:
                        # Update annotation to trigger restart
                        if not d.spec.template.metadata.annotations:
                            d.spec.template.metadata.annotations = {}
                        d.spec.template.metadata.annotations[
                            "kubectl.kubernetes.io/restartedAt"
                        ] = restart_time

                        self.apps_v1.patch_namespaced_daemon_set(
                            name=d.metadata.name, namespace=namespace, body=d
                        )
                        logger.info(
                            f"Restarted daemonset {d.metadata.name} in namespace {namespace}"
                        )
                        return True, None
            except ApiException:
                pass  # Not a daemonset

            # If we get here, no matching workload was found
            # As a last resort, try to delete pods directly (for standalone pods or Jobs)
            try:
                pods = self.v1.list_namespaced_pod(namespace)
                pods_deleted = []
                for pod in pods.items:
                    if name in pod.metadata.name:
                        self.v1.delete_namespaced_pod(
                            name=pod.metadata.name,
                            namespace=namespace,
                            grace_period_seconds=30
                        )
                        pods_deleted.append(pod.metadata.name)

                if pods_deleted:
                    logger.info(
                        f"Restarted pods {', '.join(pods_deleted)} in namespace {namespace}"
                    )
                    return True, None
            except ApiException:
                pass

            return (
                False,
                f"No workload found for service {name} in namespace {namespace}",
            )

        except Exception as e:
            error_msg = f"Failed to restart workload: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    def cleanup_stale_pods(self, namespace: str) -> int:
        """Clean up pods in Failed, Unknown, or other problematic states
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            Number of pods cleaned up
        """
        try:
            # Get all pods in namespace
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            cleaned_count = 0
            
            for pod in pods.items:
                should_delete = False
                reason = ""
                
                # Check pod phase
                if pod.status and pod.status.phase in ['Failed', 'Unknown', 'Succeeded']:
                    should_delete = True
                    reason = f"Pod phase: {pod.status.phase}"
                
                # Check for ContainerStatusUnknown
                if pod.status and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.state and cs.state.waiting and cs.state.waiting.reason == 'ContainerStatusUnknown':
                            should_delete = True
                            reason = "ContainerStatusUnknown"
                            break
                
                # Check if pod is stuck terminating (older than 1 hour)
                if pod.metadata.deletion_timestamp:
                    from datetime import datetime, timezone, timedelta
                    deletion_time = pod.metadata.deletion_timestamp.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - deletion_time > timedelta(hours=1):
                        should_delete = True
                        reason = "Stuck in terminating state"
                
                if should_delete:
                    try:
                        logger.info(f"Cleaning up stale pod {pod.metadata.name} in namespace {namespace}: {reason}")
                        self.core_v1.delete_namespaced_pod(
                            name=pod.metadata.name,
                            namespace=namespace,
                            grace_period_seconds=0
                        )
                        cleaned_count += 1
                    except ApiException as e:
                        logger.error(f"Failed to delete pod {pod.metadata.name}: {e}")
            
            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} stale pods in namespace {namespace}")
            
            return cleaned_count
            
        except ApiException as e:
            logger.error(f"Failed to cleanup stale pods in namespace {namespace}: {e}")
            return 0

    def enable_service(self, service: ServiceModel) -> Tuple[bool, Optional[str]]:
        """Enable a service by scaling to original replicas

        Args:
            service: ServiceModel model instance

        Returns:
            Tuple of (success, error_message)
        """
        if not service.can_be_disabled:
            return False, "Core services cannot be disabled"

        if service.is_enabled:
            return True, None  # Already enabled

        # Clean up any stale pods before enabling
        self.cleanup_stale_pods(service.namespace)

        # Scale to original replica count
        return self.scale_deployment(
            service.namespace, service.name, service.original_replicas
        )

    def disable_service(self, service: ServiceModel) -> Tuple[bool, Optional[str]]:
        """Disable a service by scaling to 0

        Args:
            service: ServiceModel model instance

        Returns:
            Tuple of (success, error_message)
        """
        if not service.can_be_disabled:
            return False, "Core services cannot be disabled"

        if not service.is_enabled:
            return True, None  # Already disabled

        # Get current replica count before disabling
        status = self.get_deployment_status(service.namespace, service.name)
        if status and status["replicas"] > 0:
            service.original_replicas = status["replicas"]

        # Scale to 0
        result = self.scale_deployment(service.namespace, service.name, 0)
        
        # Clean up any stale pods after disabling
        if result[0]:  # If scaling was successful
            self.cleanup_stale_pods(service.namespace)
        
        return result

    def _parse_cpu(self, cpu_str: str) -> int:
        """Parse CPU string to millicores

        Args:
            cpu_str: CPU string (e.g., "100m", "0.1", "1")

        Returns:
            CPU in millicores
        """
        if not cpu_str or cpu_str == "0":
            return 0

        if cpu_str.endswith("m"):
            return int(cpu_str[:-1])

        try:
            return int(float(cpu_str) * 1000)
        except ValueError:
            return 0

    def _parse_memory(self, memory_str: str) -> int:
        """Parse memory string to bytes

        Args:
            memory_str: Memory string (e.g., "128Mi", "1Gi", "1073741824")

        Returns:
            Memory in bytes
        """
        if not memory_str or memory_str == "0":
            return 0

        units = {
            "Ki": 1024,
            "Mi": 1024 * 1024,
            "Gi": 1024 * 1024 * 1024,
            "Ti": 1024 * 1024 * 1024 * 1024,
            "K": 1000,
            "M": 1000 * 1000,
            "G": 1000 * 1000 * 1000,
            "T": 1000 * 1000 * 1000 * 1000,
        }

        for unit, multiplier in units.items():
            if memory_str.endswith(unit):
                try:
                    return int(float(memory_str[: -len(unit)]) * multiplier)
                except ValueError:
                    return 0

        try:
            return int(memory_str)
        except ValueError:
            return 0

    def _format_memory(self, bytes_value: int) -> str:
        """Format bytes to human readable string

        Args:
            bytes_value: Memory in bytes

        Returns:
            Human readable string
        """
        if bytes_value == 0:
            return "0"

        units = ["B", "Ki", "Mi", "Gi", "Ti"]
        unit_index = 0
        value = float(bytes_value)

        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1

        if value == int(value):
            return f"{int(value)}{units[unit_index]}"
        else:
            return f"{value:.1f}{units[unit_index]}"

    def _get_deployment_info(self, deployment, namespace: str) -> Dict[str, Any]:
        """Get info for a Deployment"""
        status = {
            "name": deployment.metadata.name,
            "namespace": namespace,
            "replicas": deployment.spec.replicas,
            "ready_replicas": deployment.status.ready_replicas or 0,
            "available_replicas": deployment.status.available_replicas or 0,
            "updated_replicas": deployment.status.updated_replicas or 0,
            "workload_type": "Deployment",
            "conditions": [],
            "created_at": deployment.metadata.creation_timestamp,
        }

        # Add conditions
        if deployment.status.conditions:
            for condition in deployment.status.conditions:
                status["conditions"].append({
                    "type": condition.type,
                    "status": condition.status,
                    "reason": condition.reason,
                    "message": condition.message,
                    "last_update": condition.last_update_time,
                })

        # Get pods
        pods = self.get_pods_for_deployment(namespace, deployment.metadata.name)
        status["pods"] = pods
        self._calculate_resource_usage(status, pods)
        return status

    def _get_statefulset_info(self, statefulset, namespace: str) -> Dict[str, Any]:
        """Get info for a StatefulSet"""
        status = {
            "name": statefulset.metadata.name,
            "namespace": namespace,
            "replicas": statefulset.spec.replicas,
            "ready_replicas": statefulset.status.ready_replicas or 0,
            "available_replicas": statefulset.status.ready_replicas or 0,
            "updated_replicas": statefulset.status.updated_replicas or 0,
            "workload_type": "StatefulSet",
            "conditions": [],
            "created_at": statefulset.metadata.creation_timestamp,
        }

        # Get pods using label selector
        pods = self._get_pods_by_selector(namespace, statefulset.spec.selector.match_labels)
        status["pods"] = pods
        self._calculate_resource_usage(status, pods)
        return status

    def _get_daemonset_info(self, daemonset, namespace: str) -> Dict[str, Any]:
        """Get info for a DaemonSet"""
        status = {
            "name": daemonset.metadata.name,
            "namespace": namespace,
            "replicas": daemonset.status.desired_number_scheduled or 0,
            "ready_replicas": daemonset.status.number_ready or 0,
            "available_replicas": daemonset.status.number_available or 0,
            "updated_replicas": daemonset.status.updated_number_scheduled or 0,
            "workload_type": "DaemonSet",
            "conditions": [],
            "created_at": daemonset.metadata.creation_timestamp,
        }

        # Get pods using label selector
        pods = self._get_pods_by_selector(namespace, daemonset.spec.selector.match_labels)
        status["pods"] = pods
        self._calculate_resource_usage(status, pods)
        return status

    def _get_pods_only_info(self, pod_list, namespace: str, name: str) -> Dict[str, Any]:
        """Get info when only pods are found (no deployment/statefulset/daemonset)"""
        pods = self._process_pod_list(pod_list)
        status = {
            "name": name,
            "namespace": namespace,
            "replicas": len(pods),
            "ready_replicas": sum(1 for p in pods if p.get("ready", False)),
            "available_replicas": sum(1 for p in pods if p.get("status") == "Running"),
            "workload_type": "Pod",
            "pods": pods,
        }
        self._calculate_resource_usage(status, pods)
        return status

    def _get_pods_by_selector(self, namespace: str, labels: dict) -> List[Dict[str, Any]]:
        """Get pods using label selector"""
        pods_info = []
        try:
            # Build label selector
            label_selector = ",".join([f"{k}={v}" for k, v in labels.items()])

            # Get pods with matching labels
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            )

            pods_info = self._process_pod_list(pods.items)
        except ApiException as e:
            logger.error(f"Failed to get pods by selector: {e}")

        return pods_info

    def _process_pod_list(self, pods) -> List[Dict[str, Any]]:
        """Process a list of pod objects into pod info dicts"""
        pods_info = []

        for pod in pods:
            pod_info = {
                "name": pod.metadata.name,
                "status": pod.status.phase,
                "ready": all(c.ready for c in pod.status.container_statuses or []),
                "restart_count": sum(
                    c.restart_count for c in pod.status.container_statuses or []
                ),
                "created_at": pod.metadata.creation_timestamp,
                "node": pod.spec.node_name,
                "ip": pod.status.pod_ip,
                "containers": [],
            }

            # Get container information
            for container in pod.spec.containers:
                container_info = {
                    "name": container.name,
                    "image": container.image,
                    "resources": {},
                }

                # Get resource requests/limits
                if container.resources:
                    if container.resources.requests:
                        container_info["resources"]["cpu_request"] = (
                            container.resources.requests.get("cpu", "0")
                        )
                        container_info["resources"]["memory_request"] = (
                            container.resources.requests.get("memory", "0")
                        )
                        container_info["resources"]["gpu_request"] = (
                            container.resources.requests.get("nvidia.com/gpu", "0")
                        )
                    if container.resources.limits:
                        container_info["resources"]["cpu_limit"] = (
                            container.resources.limits.get("cpu", "0")
                        )
                        container_info["resources"]["memory_limit"] = (
                            container.resources.limits.get("memory", "0")
                        )
                        container_info["resources"]["gpu_limit"] = (
                            container.resources.limits.get("nvidia.com/gpu", "0")
                        )

                # Get container status
                for status in pod.status.container_statuses or []:
                    if status.name == container.name:
                        container_info["ready"] = status.ready
                        container_info["restart_count"] = status.restart_count
                        if status.state:
                            if status.state.running:
                                container_info["state"] = "running"
                                container_info["started_at"] = status.state.running.started_at
                            elif status.state.waiting:
                                container_info["state"] = "waiting"
                                container_info["waiting_reason"] = status.state.waiting.reason
                            elif status.state.terminated:
                                container_info["state"] = "terminated"
                                container_info["exit_code"] = status.state.terminated.exit_code
                        break

                pod_info["containers"].append(container_info)

            pods_info.append(pod_info)

        return pods_info

    def _calculate_resource_usage(self, status: dict, pods: List[Dict[str, Any]]):
        """Calculate total resource usage from pods"""
        total_cpu_requests = 0
        total_memory_requests = 0
        total_cpu_limits = 0
        total_memory_limits = 0
        total_gpu_requests = 0
        total_gpu_limits = 0
        gpu_nodes = set()

        for pod in pods:
            for container in pod.get("containers", []):
                resources = container.get("resources", {})

                # Parse CPU (convert to millicores)
                cpu_request = resources.get("cpu_request", "0")
                cpu_limit = resources.get("cpu_limit", "0")
                total_cpu_requests += self._parse_cpu(cpu_request)
                total_cpu_limits += self._parse_cpu(cpu_limit)

                # Parse memory (convert to bytes)
                memory_request = resources.get("memory_request", "0")
                memory_limit = resources.get("memory_limit", "0")
                total_memory_requests += self._parse_memory(memory_request)
                total_memory_limits += self._parse_memory(memory_limit)

                # Parse GPU count
                gpu_request = resources.get("gpu_request", "0")
                gpu_limit = resources.get("gpu_limit", "0")
                if gpu_request != "0":
                    total_gpu_requests += int(gpu_request)
                    if pod.get("node"):
                        gpu_nodes.add(pod.get("node"))
                if gpu_limit != "0":
                    total_gpu_limits += int(gpu_limit)

        status["resource_usage"] = {
            "cpu_requests_millicores": total_cpu_requests,
            "cpu_limits_millicores": total_cpu_limits,
            "memory_requests_bytes": total_memory_requests,
            "memory_limits_bytes": total_memory_limits,
            "memory_requests_human": self._format_memory(total_memory_requests),
            "memory_limits_human": self._format_memory(total_memory_limits),
            "gpu_requests": total_gpu_requests,
            "gpu_limits": total_gpu_limits,
            "gpu_nodes": list(gpu_nodes),
        }

    def describe_pod(self, namespace: str, pod_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed pod description

        Args:
            namespace: Kubernetes namespace
            pod_name: Pod name

        Returns:
            Detailed pod information with formatted text
        """
        try:
            pod = self.core_v1.read_namespaced_pod(pod_name, namespace)

            # Build detailed pod info
            pod_info = {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "uid": pod.metadata.uid,
                "creation_timestamp": pod.metadata.creation_timestamp,
                "labels": pod.metadata.labels or {},
                "annotations": pod.metadata.annotations or {},
                "node_name": pod.spec.node_name,
                "priority": pod.spec.priority,
                "service_account": pod.spec.service_account_name,
                "restart_policy": pod.spec.restart_policy,
                "dns_policy": pod.spec.dns_policy,
                "status": {
                    "phase": pod.status.phase,
                    "reason": pod.status.reason,
                    "message": pod.status.message,
                    "pod_ip": pod.status.pod_ip,
                    "host_ip": pod.status.host_ip,
                    "start_time": pod.status.start_time,
                },
                "conditions": [],
                "containers": [],
                "init_containers": [],
                "volumes": [],
                "events": []
            }

            # Add conditions
            for condition in pod.status.conditions or []:
                pod_info["conditions"].append({
                    "type": condition.type,
                    "status": condition.status,
                    "reason": condition.reason,
                    "message": condition.message,
                    "last_transition_time": condition.last_transition_time,
                })

            # Add container details
            for container in pod.spec.containers:
                container_info = {
                    "name": container.name,
                    "image": container.image,
                    "command": container.command,
                    "args": container.args,
                    "working_dir": container.working_dir,
                    "ports": [],
                    "env": [],
                    "resources": {},
                    "volume_mounts": [],
                    "liveness_probe": None,
                    "readiness_probe": None,
                }

                # Add ports
                for port in container.ports or []:
                    container_info["ports"].append({
                        "name": port.name,
                        "container_port": port.container_port,
                        "protocol": port.protocol,
                    })

                # Add environment variables
                for env in container.env or []:
                    container_info["env"].append({
                        "name": env.name,
                        "value": env.value,
                    })

                # Add resources
                if container.resources:
                    if container.resources.requests:
                        container_info["resources"]["requests"] = dict(container.resources.requests)
                    if container.resources.limits:
                        container_info["resources"]["limits"] = dict(container.resources.limits)

                # Add volume mounts
                for mount in container.volume_mounts or []:
                    container_info["volume_mounts"].append({
                        "name": mount.name,
                        "mount_path": mount.mount_path,
                        "read_only": mount.read_only,
                    })

                pod_info["containers"].append(container_info)

            # Add init containers
            for container in pod.spec.init_containers or []:
                pod_info["init_containers"].append({
                    "name": container.name,
                    "image": container.image,
                })

            # Add volumes
            for volume in pod.spec.volumes or []:
                volume_info = {"name": volume.name}
                if volume.config_map:
                    volume_info["type"] = "ConfigMap"
                    volume_info["source"] = volume.config_map.name
                elif volume.secret:
                    volume_info["type"] = "Secret"
                    volume_info["source"] = volume.secret.secret_name
                elif volume.persistent_volume_claim:
                    volume_info["type"] = "PVC"
                    volume_info["source"] = volume.persistent_volume_claim.claim_name
                elif volume.empty_dir:
                    volume_info["type"] = "EmptyDir"
                elif volume.host_path:
                    volume_info["type"] = "HostPath"
                    volume_info["path"] = volume.host_path.path
                else:
                    volume_info["type"] = "Other"

                pod_info["volumes"].append(volume_info)

            # Get events
            events = self.core_v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}"
            )

            for event in events.items:
                pod_info["events"].append({
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "count": event.count,
                    "first_timestamp": event.first_timestamp,
                    "last_timestamp": event.last_timestamp,
                    "source": f"{event.source.component}/{event.source.host}" if event.source else None,
                })

            # Add formatted description
            pod_info["formatted"] = self._format_pod_description(
                pod, pod_info["conditions"], pod_info["containers"],
                pod_info["events"], pod_info["volumes"]
            )

            return pod_info

        except ApiException as e:
            logger.error(f"Failed to describe pod: {e}")
            return None

    def _format_pod_description(self, pod, conditions, containers, events, volumes) -> str:
        """Format pod description like kubectl describe"""
        lines = []

        # Header
        lines.append(f"Name:             {pod.metadata.name}")
        lines.append(f"Namespace:        {pod.metadata.namespace}")
        lines.append(f"Priority:         {pod.spec.priority or 0}")
        lines.append(f"Service Account:  {pod.spec.service_account_name}")
        lines.append(f"Node:             {pod.spec.node_name or '<none>'}")
        lines.append(f"Start Time:       {pod.status.start_time or '<unknown>'}")

        # Labels
        if pod.metadata.labels:
            lines.append("Labels:           " + "\n                  ".join(
                [f"{k}={v}" for k, v in pod.metadata.labels.items()]
            ))
        else:
            lines.append("Labels:           <none>")

        # Annotations
        if pod.metadata.annotations:
            lines.append("Annotations:      " + "\n                  ".join(
                [f"{k}={v}" for k, v in pod.metadata.annotations.items()][:3]  # Show first 3
            ))

        # Status
        lines.append(f"Status:           {pod.status.phase}")
        if pod.status.reason:
            lines.append(f"Reason:           {pod.status.reason}")
        if pod.status.message:
            lines.append(f"Message:          {pod.status.message}")
        lines.append(f"IP:               {pod.status.pod_ip or '<none>'}")
        lines.append(f"IPs:              {pod.status.pod_ip or '<none>'}")

        # Controlled By
        if pod.metadata.owner_references:
            owner = pod.metadata.owner_references[0]
            lines.append(f"Controlled By:    {owner.kind}/{owner.name}")

        # Containers
        lines.append("\nContainers:")
        for container in containers:
            lines.append(f"  {container['name']}:")
            lines.append(f"    Container ID:   {container.get('container_id', '')}")
            lines.append(f"    Image:          {container['image']}")
            lines.append(f"    Image ID:       {container.get('image_id', '')}")

            if container.get('ports'):
                ports_str = ", ".join([f"{p['container_port']}/{p.get('protocol', 'TCP')}"
                                      for p in container['ports']])
                lines.append(f"    Port:           {ports_str}")
            else:
                lines.append(f"    Port:           <none>")

            if container.get('command'):
                lines.append(f"    Command:        {container['command']}")
            if container.get('args'):
                lines.append(f"    Args:           {container['args']}")

            lines.append(f"    State:          {container.get('state', 'Unknown')}")
            lines.append(f"    Ready:          {container.get('ready', False)}")
            lines.append(f"    Restart Count:  {container.get('restart_count', 0)}")

            # Resources
            if container.get('resources'):
                res = container['resources']
                if res.get('limits'):
                    lines.append(f"    Limits:")
                    for k, v in res['limits'].items():
                        lines.append(f"      {k}: {v}")
                if res.get('requests'):
                    lines.append(f"    Requests:")
                    for k, v in res['requests'].items():
                        lines.append(f"      {k}: {v}")

            # Environment variables (show first 5)
            if container.get('env'):
                lines.append(f"    Environment:")
                for env in container['env'][:5]:
                    lines.append(f"      {env['name']}: {env.get('value', '<set to the key>')}")
                if len(container['env']) > 5:
                    lines.append(f"      ... and {len(container['env']) - 5} more")

            # Mounts
            if container.get('volume_mounts'):
                lines.append(f"    Mounts:")
                for mount in container['volume_mounts']:
                    lines.append(f"      {mount['mount_path']} from {mount['name']} (ro={mount.get('read_only', False)})")

        # Conditions
        if conditions:
            lines.append("\nConditions:")
            lines.append("  Type              Status")
            for cond in conditions:
                lines.append(f"  {cond['type']:<16}  {cond['status']}")

        # Volumes
        if volumes:
            lines.append("\nVolumes:")
            for vol in volumes:
                lines.append(f"  {vol['name']}:")
                lines.append(f"    Type:       {vol['type']}")
                if vol.get('source'):
                    lines.append(f"    Source:     {vol['source']}")
                elif vol.get('path'):
                    lines.append(f"    Path:       {vol['path']}")

        # QoS Class
        lines.append(f"\nQoS Class:       {pod.status.qos_class or 'BestEffort'}")
        lines.append(f"Node-Selectors:  <none>")
        lines.append(f"Tolerations:     {pod.spec.tolerations[0].key if pod.spec.tolerations else 'node.kubernetes.io/not-ready for 300s'}")

        # Events
        if events:
            lines.append("\nEvents:")
            lines.append("  Type    Reason     Age   From               Message")
            lines.append("  ----    ------     ----  ----               -------")
            for event in events[-10:]:  # Show last 10 events
                event_type = event['type']
                reason = event['reason']
                # Simple age calculation
                age = "1m"  # Simplified
                source = event.get('source', 'unknown')
                message = event['message'][:80]  # Truncate long messages
                lines.append(f"  {event_type:<7} {reason:<10} {age:<5} {source:<18} {message}")
        else:
            lines.append("\nEvents:          <none>")

        return "\n".join(lines)

    def get_container_logs(
        self, namespace: str, pod_name: str, container_name: str, lines: int = 500
    ) -> str:
        """Get container logs

        Args:
            namespace: Kubernetes namespace
            pod_name: Pod name
            container_name: Container name
            lines: Number of lines to retrieve (default 500)

        Returns:
            Container logs as string
        """
        try:
            logs = self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container_name,
                tail_lines=lines,
                timestamps=True
            )
            return logs
        except ApiException as e:
            logger.error(f"Failed to get container logs: {e}")
            return f"Error retrieving logs: {str(e)}"


# 🤖 Generated with [Claude Code](https://claude.ai/code)
