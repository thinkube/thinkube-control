"""
Manifest regeneration service.

Regenerates k8s/ manifests for an already-deployed app when its thinkube.yaml changes.
Reuses the same Jinja2 templates and logic as deploy_application.py's generate_k8s_manifests().
"""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import jinja2
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Path to the J2 templates used by the deployment system
# In the backend pod, thinkube-control is mounted at /home/thinkube-control/
TEMPLATES_DIR = Path("/home/thinkube-control/templates/k8s")


def _get_k8s_client():
    """Load in-cluster config and return a CoreV1Api client."""
    config.load_incluster_config()
    return client.CoreV1Api()


def _get_custom_objects_client():
    """Load in-cluster config and return a CustomObjectsApi client."""
    config.load_incluster_config()
    return client.CustomObjectsApi()


class ManifestGenerator:
    """Generates K8s manifests from thinkube.yaml for an existing app."""

    def __init__(self, app_name: str, domain: str):
        self.app_name = app_name
        self.domain = domain
        self.namespace = app_name  # namespace == app_name by convention
        self.local_repo_path = f"/home/thinkube/apps/{self.app_name}"
        self.thinkube_config = {}
        self.secrets = {}
        self._core_v1 = None
        self._custom_objects = None

    @property
    def core_v1(self):
        if self._core_v1 is None:
            self._core_v1 = _get_k8s_client()
        return self._core_v1

    @property
    def custom_objects(self):
        if self._custom_objects is None:
            self._custom_objects = _get_custom_objects_client()
        return self._custom_objects

    def _read_secret(self, namespace: str, name: str) -> dict:
        """Read a K8s secret."""
        try:
            secret = self.core_v1.read_namespaced_secret(name, namespace)
            return secret.to_dict()
        except ApiException as e:
            raise RuntimeError(f"Failed to read secret {namespace}/{name}: {e.reason}")

    def _decode_secret(self, secret: dict, key: str) -> str:
        """Decode a base64 secret value."""
        encoded = secret.get('data', {}).get(key, '')
        if encoded:
            if isinstance(encoded, bytes):
                return encoded.decode('utf-8')
            return base64.b64decode(encoded).decode('utf-8')
        return ''

    def _fetch_secrets(self):
        """Fetch all required secrets from the cluster."""
        # Admin password
        admin_secret = self._read_secret('thinkube-control', 'admin-credentials')
        self.secrets['admin_password'] = self._decode_secret(admin_secret, 'admin-password')

        # MLflow credentials
        try:
            mlflow_secret = self._read_secret('mlflow', 'mlflow-server-credentials')
            self.secrets['mlflow_keycloak_token_url'] = self._decode_secret(mlflow_secret, 'keycloak-token-url')
            self.secrets['mlflow_keycloak_client_id'] = self._decode_secret(mlflow_secret, 'client-id')
            self.secrets['mlflow_client_secret'] = self._decode_secret(mlflow_secret, 'client-secret')
            self.secrets['mlflow_username'] = self._decode_secret(mlflow_secret, 'username')
            self.secrets['mlflow_password'] = self._decode_secret(mlflow_secret, 'password') or self.secrets['admin_password']
        except Exception as e:
            logger.warning(f"MLflow credentials not available: {e}")
            self.secrets.setdefault('mlflow_keycloak_token_url', '')
            self.secrets.setdefault('mlflow_keycloak_client_id', '')
            self.secrets.setdefault('mlflow_client_secret', '')
            self.secrets.setdefault('mlflow_username', '')
            self.secrets.setdefault('mlflow_password', self.secrets.get('admin_password', ''))

        # SeaweedFS credentials
        try:
            seaweedfs_secret = self._read_secret('seaweedfs', 'seaweedfs-s3-credentials')
            self.secrets['seaweedfs_password'] = self._decode_secret(seaweedfs_secret, 'secret_key')
            self.secrets['seaweedfs_access_key'] = self._decode_secret(seaweedfs_secret, 'access_key')
            self.secrets['seaweedfs_endpoint'] = self._decode_secret(seaweedfs_secret, 'endpoint_internal')
        except Exception as e:
            logger.warning(f"SeaweedFS credentials not available: {e}")
            self.secrets.setdefault('seaweedfs_password', '')
            self.secrets.setdefault('seaweedfs_access_key', '')
            self.secrets.setdefault('seaweedfs_endpoint', '')

    def _resolve_dependencies(self):
        """Resolve dependency URLs from the cluster."""
        dependencies = self.thinkube_config.get('spec', {}).get('dependencies', [])
        if not dependencies:
            return

        for dep in dependencies:
            dep_type = dep.get('type', '')
            resolved_url = self._find_knative_service_url(dep_type) or self._find_k8s_service_url(dep_type)
            if resolved_url:
                dep['resolved_url'] = resolved_url
            else:
                raise RuntimeError(
                    f"Dependency '{dep.get('name', '')}' (type: {dep_type}) is not deployed."
                )

    def _find_knative_service_url(self, dep_type: str) -> Optional[str]:
        """Find a Knative service URL matching the dependency type."""
        try:
            items = self.custom_objects.list_cluster_custom_object(
                group="serving.knative.dev",
                version="v1",
                plural="services",
            ).get('items', [])
            for item in items:
                name = item['metadata']['name']
                namespace = item['metadata']['namespace']
                if dep_type in name or dep_type in namespace:
                    status_url = (item.get('status', {}).get('address', {}) or {}).get('url')
                    if status_url:
                        return status_url
                    return f"http://{name}.{namespace}.svc.cluster.local"
        except Exception:
            pass
        return None

    def _find_k8s_service_url(self, dep_type: str) -> Optional[str]:
        """Find a regular K8s service URL matching the dependency type."""
        try:
            svc_list = self.core_v1.list_service_for_all_namespaces()
            skip_ns = {'kube-system', 'kube-public', 'default'}
            # First pass: match both name and namespace
            for svc in svc_list.items:
                name = svc.metadata.name
                namespace = svc.metadata.namespace
                if namespace in skip_ns:
                    continue
                if dep_type in name and dep_type in namespace:
                    ports = svc.spec.ports or []
                    port = ports[0].port if ports else 80
                    return f"http://{name}.{namespace}.svc.cluster.local:{port}"
            # Second pass: match name only
            for svc in svc_list.items:
                name = svc.metadata.name
                namespace = svc.metadata.namespace
                if namespace in skip_ns:
                    continue
                if dep_type in name:
                    ports = svc.spec.ports or []
                    port = ports[0].port if ports else 80
                    return f"http://{name}.{namespace}.svc.cluster.local:{port}"
        except Exception:
            pass
        return None

    def regenerate(self) -> Dict[str, str]:
        """Regenerate all k8s/ manifests and return them as a dict of {filename: content}.

        Also writes the files to the app's k8s/ directory.
        """
        app_path = Path(self.local_repo_path)
        thinkube_path = app_path / 'thinkube.yaml'

        if not thinkube_path.exists():
            raise FileNotFoundError(f"thinkube.yaml not found at {thinkube_path}")

        # Parse thinkube.yaml
        with open(thinkube_path, 'r') as f:
            self.thinkube_config = yaml.safe_load(f)

        # Inject metadata.name
        if 'metadata' not in self.thinkube_config:
            self.thinkube_config['metadata'] = {}
        self.thinkube_config['metadata']['name'] = self.app_name

        # Fetch cluster secrets
        self._fetch_secrets()

        # Resolve dependencies
        self._resolve_dependencies()

        # Build manifest_params from app-metadata ConfigMap (parameters from original deploy)
        manifest_params = self._read_manifest_params()

        # Setup Jinja2
        templates_dir = TEMPLATES_DIR
        if not templates_dir.exists():
            raise FileNotFoundError(f"Templates directory not found: {templates_dir}")

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(templates_dir)),
            undefined=jinja2.StrictUndefined,
            lstrip_blocks=True,
            trim_blocks=True
        )
        env.filters['to_yaml'] = lambda x: yaml.dump(x, default_flow_style=False)
        env.filters['to_json'] = lambda x: json.dumps(x)

        container_registry = f"registry.{self.domain}"
        admin_username = os.environ.get('ADMIN_USERNAME', 'tkadmin')

        template_vars = {
            'project_name': self.app_name,
            'k8s_namespace': self.namespace,
            'domain_name': self.domain,
            'container_registry': container_registry,
            'admin_username': admin_username,
            'admin_password': self.secrets['admin_password'],
            'thinkube_spec': self.thinkube_config,
            'manifest_params': manifest_params,
            'mlflow_keycloak_token_url': self.secrets.get('mlflow_keycloak_token_url', ''),
            'mlflow_keycloak_client_id': self.secrets.get('mlflow_keycloak_client_id', ''),
            'mlflow_client_secret': self.secrets.get('mlflow_client_secret', ''),
            'mlflow_username': self.secrets.get('mlflow_username', ''),
            'mlflow_password': self.secrets.get('mlflow_password', ''),
            'seaweedfs_password': self.secrets.get('seaweedfs_password', ''),
            'seaweedfs_access_key': self.secrets.get('seaweedfs_access_key', ''),
            'seaweedfs_endpoint': self.secrets.get('seaweedfs_endpoint', ''),
        }

        k8s_dir = app_path / 'k8s'
        k8s_dir.mkdir(parents=True, exist_ok=True)

        generated_files = {}

        # Determine deployment type
        deployment_config = self.thinkube_config.get('spec', {}).get('deployment', {})
        deployment_type = deployment_config.get('type', 'app')
        is_knative = deployment_type == 'knative'
        containers = self.thinkube_config.get('spec', {}).get('containers', [])
        services = self.thinkube_config.get('spec', {}).get('services', [])
        has_database = 'database' in services
        needs_storage = (
            'storage' in services or
            any(c.get('gpu', {}).get('count') for c in containers) or
            any('volume' in c for c in containers)
        )

        # 1. namespace.yaml
        generated_files['namespace.yaml'] = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {self.namespace}
  labels:
    app.kubernetes.io/name: {self.app_name}
    app.kubernetes.io/managed-by: argocd
"""

        # 2. mlflow-secrets.yaml
        generated_files['mlflow-secrets.yaml'] = env.get_template('mlflow-secrets.j2').render(**template_vars)

        # 3. app-metadata.yaml
        containers_json = json.dumps(containers)
        generated_files['app-metadata.yaml'] = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {self.app_name}-metadata
  namespace: {self.namespace}
data:
  app_name: "{self.app_name}"
  containers: |
    {containers_json}
"""

        if is_knative:
            generated_files['knative-service.yaml'] = env.get_template('knative-service.j2').render(**template_vars)
        else:
            generated_files['deployments.yaml'] = env.get_template('deployment-separate.j2').render(**template_vars)
            generated_files['services.yaml'] = env.get_template('services-separate.j2').render(**template_vars)
            generated_files['ingress.yaml'] = env.get_template('httproute.j2').render(**template_vars)
            generated_files['paused-backend.yaml'] = env.get_template('paused-backend.yaml.j2').render(**template_vars)

        if has_database:
            generated_files['postgresql.yaml'] = env.get_template('postgresql.j2').render(**template_vars)

        if needs_storage:
            generated_files['storage-pvc.yaml'] = env.get_template('storage-pvc.j2').render(**template_vars)

        # build-workflow.yaml
        system_username = os.environ.get('SYSTEM_USERNAME')
        master_node_name = os.environ.get('MASTER_NODE_NAME')
        if not system_username:
            raise ValueError("SYSTEM_USERNAME env var not set")
        if not master_node_name:
            raise ValueError("MASTER_NODE_NAME env var not set")
        workflow_vars = {**template_vars, 'system_username': system_username, 'master_node_name': master_node_name}
        generated_files['build-workflow.yaml'] = env.get_template('build-workflow.j2').render(**workflow_vars)

        # kustomization.yaml
        if is_knative:
            kustomization_resources = [
                'namespace.yaml', 'mlflow-secrets.yaml', 'app-metadata.yaml',
                'knative-service.yaml',
            ]
        else:
            kustomization_resources = [
                'namespace.yaml', 'mlflow-secrets.yaml', 'app-metadata.yaml',
                'deployments.yaml', 'services.yaml', 'ingress.yaml',
            ]
        if has_database:
            kustomization_resources.append('postgresql.yaml')
        if needs_storage:
            kustomization_resources.append('storage-pvc.yaml')
        kustomization_resources.append('argocd-postsync-hook.yaml')

        images_list = []
        for container in containers:
            images_list.append(f"  - name: {container_registry}/thinkube/{self.app_name}-{container['name']}\n    newTag: latest")

        generated_files['kustomization.yaml'] = f"""apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
{chr(10).join(f"  - {r}" for r in kustomization_resources)}

images:
{chr(10).join(images_list)}
"""

        # argocd-postsync-hook.yaml — read existing one rather than regenerate
        # (it contains deployment reporting logic that doesn't change)
        existing_postsync = k8s_dir / 'argocd-postsync-hook.yaml'
        if existing_postsync.exists():
            generated_files['argocd-postsync-hook.yaml'] = existing_postsync.read_text()

        # Write all files to k8s/
        for filename, content in generated_files.items():
            (k8s_dir / filename).write_text(content)

        logger.info(f"Regenerated {len(generated_files)} manifest files for {self.app_name}")
        return generated_files

    def _read_manifest_params(self) -> Dict[str, str]:
        """Read manifest parameters from the app-metadata ConfigMap on the cluster.

        These are the template-specific parameters (e.g., model_id) that were
        provided at deploy time and should be preserved during regeneration.
        """
        try:
            cm = self.core_v1.read_namespaced_config_map(
                f'{self.app_name}-metadata', self.namespace
            )
            data = cm.data or {}
            known_keys = {'app_name', 'containers'}
            return {k: v for k, v in data.items() if k not in known_keys}
        except ApiException as e:
            logger.warning(f"Could not read app-metadata ConfigMap: {e.reason}")
            return {}
        except Exception as e:
            logger.warning(f"Could not read manifest params: {e}")
            return {}
