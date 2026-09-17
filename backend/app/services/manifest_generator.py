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

import sys

import jinja2
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

sys.path.insert(0, '/home/thinkube/thinkube-control/scripts')
from thinkube_yaml_validator import (
    validate_knative_constraints as _validate_knative_constraints,
    validate_component_constraints as _validate_component_constraints,
    validate_replicas as _validate_replicas,
)
from manifest_plan import kustomization_content as _kustomization_content, kustomization_resources as _kustomization_resources
from platform_credentials import (
    RETIRED_MANIFESTS as _RETIRED_MANIFESTS,
    PlatformValues as _PlatformValues,
    platform_secrets as _platform_secrets,
)
from manifest_parameters import (
    PARAMETERS_KEY as _PARAMETERS_KEY,
    encode as _encode_parameters,
    declared_parameter_names as _declared_parameter_names,
    recorded_values as _recorded_parameter_values,
)
from namespace_quota import NON_GPU_QUOTA as _NON_GPU_QUOTA, memory_quota as _memory_quota, node_view as _node_view
from app_secrets import (
    declared_secrets as _declared_secrets_of,
    env_from as _env_from,
    refusal as _secrets_refusal,
    secret_data as _secret_data,
    secret_manifest as _secret_manifest,
    secret_resource_name as _secret_resource_name,
)

logger = logging.getLogger(__name__)

# Path to the J2 templates used by the deployment system
# The backend pod mounts the shared home at /home/thinkube, where the
# thinkube-control checkout lives — the same path code-server sees.
TEMPLATES_DIR = Path("/home/thinkube/thinkube-control/templates/k8s")


def _get_k8s_client():
    """Load in-cluster config and return a CoreV1Api client."""
    config.load_incluster_config()
    return client.CoreV1Api()


def _gpu_namespace_quota(has_gpu):
    """(requests.memory, limits.memory) for a namespace. See scripts/namespace_quota.py."""
    if not has_gpu:
        return _NON_GPU_QUOTA
    config.load_incluster_config()
    nodes = [
        _node_view(n.metadata.name, n.status.allocatable, n.metadata.labels)
        for n in client.CoreV1Api().list_node().items
    ]
    return _memory_quota(True, nodes, os.environ)


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
        """Decode a base64 secret value. The key must be present and non-empty."""
        encoded = (secret.get('data') or {}).get(key)
        if not encoded:
            name = secret.get('metadata', {})
            raise RuntimeError(
                f"Secret {name.get('namespace')}/{name.get('name')} has no value for '{key}'"
            )
        if isinstance(encoded, bytes):
            return encoded.decode('utf-8')
        return base64.b64decode(encoded).decode('utf-8')

    def _fetch_secrets(self):
        """Read the secrets the manifests are rendered with. Each must exist."""
        admin_secret = self._read_secret('thinkube-control', 'admin-credentials')
        self.secrets['admin_username'] = self._decode_secret(admin_secret, 'admin-username')
        self.secrets['admin_password'] = self._decode_secret(admin_secret, 'admin-password')

        # The secret the first deploy reads in scripts/deploy_application.py.
        mlflow_secret = self._read_secret('thinkube-control', 'mlflow-auth-config')
        self.secrets['mlflow_keycloak_token_url'] = self._decode_secret(mlflow_secret, 'keycloak-token-url')
        self.secrets['mlflow_keycloak_client_id'] = self._decode_secret(mlflow_secret, 'client-id')
        self.secrets['mlflow_client_secret'] = self._decode_secret(mlflow_secret, 'client-secret')
        self.secrets['mlflow_username'] = self._decode_secret(mlflow_secret, 'username')
        self.secrets['mlflow_password'] = self._decode_secret(mlflow_secret, 'password')

        seaweedfs_secret = self._read_secret('seaweedfs', 'seaweedfs-s3-credentials')
        self.secrets['seaweedfs_password'] = self._decode_secret(seaweedfs_secret, 'secret_key')
        self.secrets['seaweedfs_access_key'] = self._decode_secret(seaweedfs_secret, 'access_key')
        self.secrets['seaweedfs_endpoint'] = self._decode_secret(seaweedfs_secret, 'endpoint_internal')

    def _apply_platform_credentials(self):
        """Write the database, Experiments, Storage and build credentials as Secrets.

        The manifests in k8s/ name these Secrets and carry no value, so the
        generated files a repository commits hold no credential.
        """
        from app.services.deploy_identity import deploy_api_client

        values = _PlatformValues(
            admin_username=self.secrets['admin_username'],
            admin_password=self.secrets['admin_password'],
            mlflow_keycloak_token_url=self.secrets['mlflow_keycloak_token_url'],
            mlflow_keycloak_client_id=self.secrets['mlflow_keycloak_client_id'],
            mlflow_client_secret=self.secrets['mlflow_client_secret'],
            mlflow_username=self.secrets['mlflow_username'],
            mlflow_password=self.secrets['mlflow_password'],
            seaweedfs_endpoint=self.secrets['seaweedfs_endpoint'],
            seaweedfs_access_key=self.secrets['seaweedfs_access_key'],
            seaweedfs_secret_key=self.secrets['seaweedfs_password'],
        )
        services = self.thinkube_config.get('spec', {}).get('services', []) or []
        writer = client.CoreV1Api(deploy_api_client())
        for body in _platform_secrets(
            self.app_name, self.namespace, values,
            has_database='database' in services, has_workflows='workflows' in services,
        ):
            name, namespace = body['metadata']['name'], body['metadata']['namespace']
            try:
                writer.replace_namespaced_secret(name, namespace, body)
            except ApiException as e:
                if e.status != 404:
                    raise RuntimeError(f"Cannot update Secret {namespace}/{name}: {e.reason}") from e
                writer.create_namespaced_secret(namespace, body)

    def _resolve_dependencies(self):
        """Resolve dependency URLs from the cluster."""
        dependencies = self.thinkube_config.get('spec', {}).get('dependencies', [])
        if not dependencies:
            return

        for dep in dependencies:
            dep_type = dep.get('type')
            if not dep_type:
                raise ValueError(f"Dependency '{dep.get('name')}' has no type; thinkube.yaml requires one.")
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
        except ApiException as e:
            # Knative is an optional component. Without it the API has no
            # Knative services resource, so no Knative service can match.
            if e.status == 404:
                return None
            raise RuntimeError(f"Listing Knative services failed: {e.reason}") from e
        for item in items:
            name = item['metadata']['name']
            namespace = item['metadata']['namespace']
            if dep_type in name or dep_type in namespace:
                status_url = ((item.get('status') or {}).get('address') or {}).get('url')
                if not status_url:
                    raise RuntimeError(
                        f"Knative service {namespace}/{name} matches dependency type "
                        f"'{dep_type}' but has no address yet: it is not ready."
                    )
                return status_url
        return None

    def _find_k8s_service_url(self, dep_type: str) -> Optional[str]:
        """Find a regular K8s service URL matching the dependency type."""
        try:
            svc_list = self.core_v1.list_service_for_all_namespaces()
        except ApiException as e:
            raise RuntimeError(f"Listing services failed: {e.reason}") from e
        skip_ns = {'kube-system', 'kube-public', 'default'}
        candidates = [s for s in svc_list.items if s.metadata.namespace not in skip_ns]
        # First pass: match both name and namespace
        match = next(
            (s for s in candidates if dep_type in s.metadata.name and dep_type in s.metadata.namespace),
            None,
        )
        # Second pass: match name only
        if match is None:
            match = next((s for s in candidates if dep_type in s.metadata.name), None)
        if match is None:
            return None
        name, namespace = match.metadata.name, match.metadata.namespace
        if not match.spec.ports:
            raise RuntimeError(
                f"Service {namespace}/{name} matches dependency type '{dep_type}' but exposes no port."
            )
        return f"http://{name}.{namespace}.svc.cluster.local:{match.spec.ports[0].port}"

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

        # Validate Knative portability constraints
        violations = _validate_knative_constraints(self.thinkube_config)
        violations.extend(_validate_component_constraints(self.thinkube_config))
        violations.extend(_validate_replicas(self.thinkube_config))
        if violations:
            msg = "thinkube.yaml validation failed:\n" + "\n".join(f"  - {v}" for v in violations)
            raise ValueError(msg)

        # Secrets declared in manifest.yaml, checked against the store by name
        self.declared_secrets = self._declared_secrets(app_path)
        problems = _secrets_refusal(
            self.app_name, self.domain, self.thinkube_config, self.declared_secrets,
            self._present_secret_names(self.declared_secrets),
        )
        if problems:
            raise ValueError("Secrets cannot be delivered:\n" + "\n".join(f"  - {p}" for p in problems))
        self._apply_app_secret()

        # Set replicas default at parse time (not in Jinja2 templates)
        deployment = self.thinkube_config.get('spec', {}).get('deployment', {})
        if 'replicas' not in deployment:
            if 'deployment' not in self.thinkube_config.get('spec', {}):
                self.thinkube_config['spec']['deployment'] = {}
            self.thinkube_config['spec']['deployment']['replicas'] = 1

        # Fetch cluster secrets, and write the ones the manifests name
        self._fetch_secrets()
        self._apply_platform_credentials()

        # Resolve dependencies
        self._resolve_dependencies()

        # The declared parameters, with the values the app was deployed with
        manifest_params = self._read_manifest_params(app_path)

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
        template_vars = {
            'project_name': self.app_name,
            'k8s_namespace': self.namespace,
            'domain_name': self.domain,
            'container_registry': container_registry,
            'admin_username': self.secrets['admin_username'],
            'thinkube_spec': self.thinkube_config,
            'manifest_params': manifest_params,
            'seaweedfs_endpoint': self.secrets['seaweedfs_endpoint'],
            'deployment_env_from': _env_from(self.app_name, self.declared_secrets),
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
        has_workflows = 'workflows' in services
        has_gpu = any(c.get('gpu', {}).get('count') for c in containers)
        quota_req_mem, quota_lim_mem = _gpu_namespace_quota(has_gpu)
        needs_storage = (
            'storage' in services or
            has_gpu or
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

        # 1b. resource-policies.yaml (LimitRange + ResourceQuota)
        generated_files['resource-policies.yaml'] = f"""apiVersion: v1
kind: LimitRange
metadata:
  name: default-resources
  namespace: {self.namespace}
spec:
  limits:
    - type: Container
      default:
        memory: "256Mi"
        cpu: "250m"
      defaultRequest:
        memory: "64Mi"
        cpu: "25m"
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: resource-budget
  namespace: {self.namespace}
spec:
  hard:
    requests.memory: "{quota_req_mem}"
    limits.memory: "{quota_lim_mem}"
    requests.cpu: "4"
    limits.cpu: "8"
"""

        # 2. Files an earlier generator wrote with credentials in them
        for retired in _RETIRED_MANIFESTS:
            (k8s_dir / retired).unlink(missing_ok=True)

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
  {_PARAMETERS_KEY}: {json.dumps(_encode_parameters(manifest_params))}
"""

        if is_knative:
            generated_files['knative-service.yaml'] = env.get_template('knative-service.j2').render(**template_vars)
        else:
            generated_files['deployments.yaml'] = env.get_template('deployment-separate.j2').render(**template_vars)
            generated_files['services.yaml'] = env.get_template('services-separate.j2').render(**template_vars)
            generated_files['ingress.yaml'] = env.get_template('httproute.j2').render(**template_vars)
            generated_files['paused-backend.yaml'] = env.get_template('paused-backend.yaml.j2').render(**template_vars)

        if needs_storage:
            generated_files['storage-pvc.yaml'] = env.get_template('storage-pvc.j2').render(**template_vars)

        if has_workflows:
            generated_files['workflows.yaml'] = env.get_template('workflows.j2').render(**template_vars)

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
        generated_files['kustomization.yaml'] = _kustomization_content(
            app_name=self.app_name,
            container_registry=container_registry,
            containers=containers,
            resources=_kustomization_resources(
                is_knative=is_knative,
                needs_storage=needs_storage,
                has_workflows=has_workflows,
            ),
            has_workflows=has_workflows,
        )

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

    @staticmethod
    def _declared_secrets(app_path: Path):
        """The secrets manifest.yaml declares. Every application has a manifest."""
        manifest_path = app_path / 'manifest.yaml'
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.yaml not found at {manifest_path}")
        with open(manifest_path, 'r') as f:
            return _declared_secrets_of(yaml.safe_load(f))

    def _apply_app_secret(self):
        """Make <app>-secrets hold the declared secrets the store holds, and record the usage.

        An application that declares none has no such Secret: one left from an
        earlier declaration is removed.
        """
        from app.db.session import get_session_local
        from app.models.secrets import Secret
        from app.services.app_secret_usage import record_usage
        from app.services.secrets_service import secrets_service

        name = _secret_resource_name(self.app_name)
        db = get_session_local()()
        try:
            names = [s.name for s in self.declared_secrets]
            stored = {
                row.name: row
                for row in (db.query(Secret).filter(Secret.name.in_(names)).all() if names else [])
            }
            data = _secret_data(
                self.declared_secrets,
                lambda n: secrets_service.decrypt(stored[n].encrypted_value) if n in stored else None,
            )

            from app.services.deploy_identity import deploy_api_client

            writer = client.CoreV1Api(deploy_api_client())
            if not self.declared_secrets:
                try:
                    writer.delete_namespaced_secret(name, self.namespace)
                except ApiException as e:
                    if e.status != 404:
                        raise RuntimeError(f"Cannot remove Secret {self.namespace}/{name}: {e.reason}") from e
            else:
                body = _secret_manifest(self.app_name, self.namespace, data)
                try:
                    writer.replace_namespaced_secret(name, self.namespace, body)
                except ApiException as e:
                    if e.status != 404:
                        raise RuntimeError(f"Cannot update Secret {self.namespace}/{name}: {e.reason}") from e
                    writer.create_namespaced_secret(self.namespace, body)

            record_usage(db, self.app_name, sorted(data))
        finally:
            db.close()

    @staticmethod
    def _present_secret_names(declared) -> set:
        """Which declared names the Secrets store holds. Only those names are asked."""
        if not declared:
            return set()
        from app.db.session import get_session_local
        from app.models.secrets import Secret

        db = get_session_local()()
        try:
            rows = db.query(Secret.name).filter(Secret.name.in_([s.name for s in declared])).all()
            return {row[0] for row in rows}
        finally:
            db.close()

    def _read_manifest_params(self, app_path: Path) -> Dict[str, str]:
        """The parameters manifest.yaml declares, with the values the app was deployed with.

        The values are in the app-metadata ConfigMap. Regenerating without them
        would drop them from the manifests, so a ConfigMap that cannot be read
        stops the regeneration.
        """
        with open(app_path / 'manifest.yaml', 'r') as f:
            names = _declared_parameter_names(yaml.safe_load(f))
        if not names:
            return {}
        name = f'{self.app_name}-metadata'
        try:
            cm = self.core_v1.read_namespaced_config_map(name, self.namespace)
        except ApiException as e:
            raise RuntimeError(
                f"Cannot read ConfigMap {self.namespace}/{name}, which holds the "
                f"parameters {self.app_name} was deployed with: {e.reason}"
            ) from e
        return _recorded_parameter_values(names, cm.data or {}, self.app_name)
