#!/usr/bin/env python3
"""
Fast parallel application deployment script for Thinkube.
Replaces the Ansible playbook with optimized Python code.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import jinja2
import yaml
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException


class DeploymentLogger:
    """Handles real-time logging with timestamps."""

    @staticmethod
    def log(message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [{level}] {message}", flush=True)

    @staticmethod
    def error(message: str):
        DeploymentLogger.log(message, "ERROR")

    @staticmethod
    def success(message: str):
        DeploymentLogger.log(f"✅ {message}", "SUCCESS")

    @staticmethod
    def phase(phase_num: int, message: str):
        DeploymentLogger.log(f"🚀 PHASE {phase_num}: {message}", "PHASE")


class ApplicationDeployer:
    """Main deployment orchestrator."""

    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.app_name = params['app_name']
        self.namespace = params['deployment_namespace']
        self.domain = params['domain_name']
        self.admin_username = params['admin_username']
        self.template_url = params['template_url']
        # Inside container: /home is mounted from host's /home/{ansible_user}/shared-code
        self.local_repo_path = f"/home/{self.app_name}"

        # Will be populated during deployment
        self.secrets = {}
        self.thinkube_config = {}
        self.k8s_core = None
        self.k8s_custom = None

    async def initialize_k8s_clients(self):
        """Initialize Kubernetes async clients."""
        await config.load_kube_config(config_file=self.params.get('kubeconfig'))
        self.k8s_core = client.CoreV1Api()
        self.k8s_custom = client.CustomObjectsApi()
        self.k8s_apps = client.AppsV1Api()

    async def cleanup_k8s_clients(self):
        """Close K8s client connections."""
        if self.k8s_core:
            await self.k8s_core.api_client.close()
        if self.k8s_custom:
            await self.k8s_custom.api_client.close()
        if self.k8s_apps:
            await self.k8s_apps.api_client.close()

    # ==================== PHASE 1: Setup & Validation ====================

    async def phase1_setup(self):
        """Phase 1: Validate parameters and run Copier."""
        DeploymentLogger.phase(1, "Setup & Validation")

        # Validate required parameters
        required = ['app_name', 'template_url', 'domain_name', 'admin_username', 'github_token']
        for param in required:
            if not self.params.get(param):
                raise ValueError(f"Required parameter '{param}' is missing")

        DeploymentLogger.log(f"Deploying {self.app_name} to namespace {self.namespace}")

        # Create namespace
        await self.create_namespace()

        # Run Copier
        await self.run_copier()

        DeploymentLogger.success("Phase 1 complete")

    async def create_namespace(self):
        """Create application namespace if it doesn't exist."""
        try:
            await self.k8s_core.read_namespace(self.namespace)
            DeploymentLogger.log(f"Namespace {self.namespace} already exists")
        except ApiException as e:
            if e.status == 404:
                namespace = client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=self.namespace)
                )
                await self.k8s_core.create_namespace(namespace)
                DeploymentLogger.success(f"Created namespace {self.namespace}")
            else:
                raise

    async def run_copier(self):
        """Run Copier to process the template."""
        DeploymentLogger.log(f"Running Copier for template: {self.template_url}")

        # Build copier command with all template parameters
        copier_cmd = [
            "copier", "copy",
            "--force",
            "--vcs-ref=HEAD",
            self.template_url,
            self.local_repo_path,
            "--data", f"domain_name={self.domain}",
            "--data", f"admin_username={self.admin_username}",
            "--data", f"namespace={self.namespace}",
        ]

        # Add all other parameters from self.params
        for key, value in self.params.items():
            if key not in ['app_name', 'template_url', 'deployment_namespace', 'domain_name', 'admin_username']:
                copier_cmd.extend(["--data", f"{key}={value}"])

        # Run copier
        process = await asyncio.create_subprocess_exec(
            *copier_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=Path(self.local_repo_path).parent
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            DeploymentLogger.error(f"Copier failed: {stderr.decode()}")
            raise RuntimeError("Copier execution failed")

        DeploymentLogger.success("Copier processing complete")

    # ==================== PHASE 2: Resource Gathering ====================

    async def phase2_gather_resources(self):
        """Phase 2: Fetch all required resources in parallel."""
        DeploymentLogger.phase(2, "Resource Gathering (Parallel)")

        # Run all fetch operations concurrently (except those with dependencies)
        results = await asyncio.gather(
            self.get_wildcard_cert(),
            self.get_harbor_credentials(),
            self.get_admin_credentials(),
            self.get_cicd_api_token(),
            self.get_mlflow_credentials(),
            self.get_seaweedfs_credentials(),
            self.get_argocd_credentials(),
            self.get_gitea_token(),
            self.parse_thinkube_yaml(),
            return_exceptions=True
        )

        # Check for any failures
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                DeploymentLogger.error(f"Resource gathering failed: {result}")
                raise result

        DeploymentLogger.success("Phase 2 complete - all resources gathered")

    async def get_wildcard_cert(self):
        """Fetch wildcard TLS certificate."""
        cert_name = self.domain.replace('.', '-') + '-tls'
        try:
            secret = await self.k8s_core.read_namespaced_secret(cert_name, 'default')
            self.secrets['wildcard_cert'] = secret
            DeploymentLogger.log(f"Retrieved wildcard certificate: {cert_name}")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get wildcard cert: {e}")
            raise

    def _decode_secret_data(self, secret, key: str) -> str:
        """Helper to decode base64 secret data."""
        encoded = secret.data.get(key)
        if encoded:
            return base64.b64decode(encoded).decode('utf-8')
        return None

    async def get_harbor_credentials(self):
        """Fetch Harbor robot credentials."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('harbor-robot-credentials', 'kube-system')
            self.secrets['harbor'] = secret
            DeploymentLogger.log("Retrieved Harbor credentials")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get Harbor credentials: {e}")
            raise

    async def get_admin_credentials(self):
        """Fetch admin credentials."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('admin-credentials', 'thinkube-control')
            self.secrets['admin'] = secret
            DeploymentLogger.log("Retrieved admin credentials")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get admin credentials: {e}")
            raise

    async def get_cicd_api_token(self):
        """Fetch CI/CD API token."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('cicd-monitoring-token', 'thinkube-control')
            self.secrets['cicd_token'] = secret
            DeploymentLogger.log("Retrieved CI/CD API token")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get CI/CD token: {e}")
            raise

    async def get_mlflow_credentials(self):
        """Fetch MLflow credentials."""
        try:
            # mlflow-auth-config is a secret containing username, password, client-id, client-secret, keycloak-token-url
            secret = await self.k8s_core.read_namespaced_secret('mlflow-auth-config', 'thinkube-control')
            self.secrets['mlflow'] = {'secret': secret}
            DeploymentLogger.log("Retrieved MLflow credentials")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get MLflow credentials: {e}")
            raise

    async def get_seaweedfs_credentials(self):
        """Fetch SeaweedFS credentials."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('seaweedfs-s3-credentials', 'seaweedfs')
            self.secrets['seaweedfs'] = secret
            DeploymentLogger.log("Retrieved SeaweedFS credentials")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get SeaweedFS credentials: {e}")
            raise

    async def get_argocd_credentials(self):
        """Fetch ArgoCD credentials."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('argocd-credentials', 'thinkube-control')
            self.secrets['argocd'] = secret
            DeploymentLogger.log("Retrieved ArgoCD credentials")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get ArgoCD credentials: {e}")
            raise

    async def get_gitea_token(self):
        """Fetch Gitea admin token."""
        try:
            secret = await self.k8s_core.read_namespaced_secret('gitea-admin-token', 'gitea')
            self.secrets['gitea'] = secret
            DeploymentLogger.log("Retrieved Gitea admin token")
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get Gitea token: {e}")
            raise

    async def parse_thinkube_yaml(self):
        """Parse thinkube.yaml configuration."""
        config_path = Path(self.local_repo_path) / "thinkube.yaml"
        try:
            with open(config_path, 'r') as f:
                self.thinkube_config = yaml.safe_load(f)
            DeploymentLogger.log("Parsed thinkube.yaml configuration")
        except Exception as e:
            DeploymentLogger.error(f"Failed to parse thinkube.yaml: {e}")
            raise

    async def ensure_gitea_repo(self):
        """Ensure Gitea repository exists (create only if missing - match Ansible)."""
        gitea_token = self._decode_secret_data(self.secrets['gitea'], 'token')
        gitea_hostname = f"git.{self.domain}"
        org = "thinkube-deployments"

        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'token {gitea_token}',
                'Content-Type': 'application/json'
            }

            # Check if repository exists
            repo_url = f"https://{gitea_hostname}/api/v1/repos/{org}/{self.app_name}"
            async with session.get(repo_url, headers=headers, ssl=False) as resp:
                if resp.status == 200:
                    DeploymentLogger.log(f"Gitea repository already exists: {org}/{self.app_name}")
                    return

            # Create repository only if it doesn't exist
            create_url = f"https://{gitea_hostname}/api/v1/orgs/{org}/repos"
            repo_payload = {
                'name': self.app_name,
                'description': f'Deployment manifests for {self.app_name}',
                'private': True,
                'auto_init': False
            }

            async with session.post(create_url, headers=headers, json=repo_payload, ssl=False) as resp:
                if resp.status == 201:
                    DeploymentLogger.log(f"Created Gitea repository: {org}/{self.app_name}")
                else:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to create Gitea repo: {resp.status} - {error_text}")
                    raise RuntimeError(f"Failed to create Gitea repository: {resp.status}")

    # ==================== PHASE 3: Resource Creation ====================

    async def phase3_create_resources(self):
        """Phase 3: Create all K8s resources in parallel."""
        DeploymentLogger.phase(3, "Resource Creation (Parallel)")

        # Create all resources concurrently
        await asyncio.gather(
            self.create_tls_secret(),
            self.create_harbor_secret(),
            self.create_postgres_secret(),
            self.create_cicd_secrets(),
            self.create_mlflow_config(),
            self.create_app_metadata(),
            self.manage_databases(),
            self.create_keycloak_client(),
            self.deploy_workflow_template(),
            return_exceptions=False
        )

        DeploymentLogger.success("Phase 3 complete - all resources created")

    async def create_tls_secret(self):
        """Copy TLS certificate to application namespace (name: {namespace}-tls-secret to match Ansible)."""
        cert = self.secrets['wildcard_cert']
        tls_secret_name = f"{self.namespace}-tls-secret"
        new_secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name=tls_secret_name, namespace=self.namespace),
            data=cert.data,
            type=cert.type
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, new_secret)
            DeploymentLogger.log(f"Created TLS secret: {tls_secret_name}")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log(f"TLS secret {tls_secret_name} already exists")
            else:
                raise

    async def create_harbor_secret(self):
        """Create Harbor robot secret in BOTH argo and app namespaces (matches Ansible)."""
        harbor = self.secrets['harbor']

        # Create in both namespaces (argo for Kaniko builds, app namespace for pods)
        for ns in ['argo', self.namespace]:
            new_secret = client.V1Secret(
                metadata=client.V1ObjectMeta(name='harbor-docker-config', namespace=ns),
                data=harbor.data,
                type=harbor.type
            )
            try:
                await self.k8s_core.create_namespaced_secret(ns, new_secret)
                DeploymentLogger.log(f"Created Harbor secret in {ns}")
            except ApiException as e:
                if e.status == 409:
                    DeploymentLogger.log(f"Harbor secret already exists in {ns}")

    async def create_postgres_secret(self):
        """Create or replace PostgreSQL credentials secret (name matches Ansible: postgresql-official)."""
        admin_secret = self.secrets['admin']
        postgres_password = self._decode_secret_data(admin_secret, 'admin-password')
        postgres_username = self._decode_secret_data(admin_secret, 'admin-username')

        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name='postgresql-official', namespace=self.namespace),
            string_data={
                'username': postgres_username,
                'password': postgres_password,
                'database': f'{self.app_name}_prod'
            }
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, secret)
            DeploymentLogger.log("Created PostgreSQL secret: postgresql-official")
        except ApiException as e:
            if e.status == 409:
                # Replace existing secret to ensure correct credentials
                await self.k8s_core.replace_namespaced_secret('postgresql-official', self.namespace, secret)
                DeploymentLogger.log("Replaced existing PostgreSQL secret")

    async def create_cicd_secrets(self):
        """Create CI/CD token secrets in both argo and app namespaces."""
        cicd_token = self._decode_secret_data(self.secrets['cicd_token'], 'token')

        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name=f'{self.app_name}-cicd-token'),
            string_data={'token': cicd_token}
        )

        for ns in ['argo', self.namespace]:
            secret.metadata.namespace = ns
            try:
                await self.k8s_core.create_namespaced_secret(ns, secret)
                DeploymentLogger.log(f"Created CI/CD secret in {ns}")
            except ApiException as e:
                if e.status == 409:
                    DeploymentLogger.log(f"CI/CD secret already exists in {ns}")

    async def create_mlflow_config(self):
        """Create MLflow configuration secret in target namespace."""
        mlflow_secret = self.secrets['mlflow']['secret']
        # Copy the secret data to the target namespace
        new_secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name='mlflow-auth-config', namespace=self.namespace),
            data=mlflow_secret.data  # Already base64 encoded
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, new_secret)
            DeploymentLogger.log("Created MLflow config secret")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("MLflow config secret already exists")

    async def create_app_metadata(self):
        """Create application metadata ConfigMap."""
        metadata = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=f'{self.app_name}-metadata', namespace=self.namespace),
            data={
                'app_name': self.app_name,
                'domain': self.domain,
                'namespace': self.namespace,
                'config': yaml.dump(self.thinkube_config)
            }
        )
        try:
            await self.k8s_core.create_namespaced_config_map(self.namespace, metadata)
            DeploymentLogger.log(f"Created app metadata: {self.app_name}-metadata")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("App metadata already exists")

    async def create_keycloak_client(self):
        """Create Keycloak OIDC client for the application (matches Ansible keycloak_client role)."""
        admin_username = self._decode_secret_data(self.secrets['admin'], 'admin-username')
        admin_password = self._decode_secret_data(self.secrets['admin'], 'admin-password')
        keycloak_url = f"https://auth.{self.domain}"
        keycloak_realm = self.params.get('keycloak_realm', 'thinkube')
        client_id = self.namespace  # Match Ansible: keycloak_app_client_id = namespace
        app_host = f"{self.app_name}.{self.domain}"

        async with aiohttp.ClientSession() as session:
            # Step 1: Get Keycloak admin token
            token_url = f"{keycloak_url}/realms/master/protocol/openid-connect/token"
            token_data = {
                'client_id': 'admin-cli',
                'username': admin_username,
                'password': admin_password,
                'grant_type': 'password'
            }

            async with session.post(token_url, data=token_data, ssl=False) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to get Keycloak admin token: {resp.status} - {error_text}")
                    raise RuntimeError("Failed to get Keycloak admin token")
                token_response = await resp.json()
                access_token = token_response['access_token']

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }

            # Step 2: Check if client already exists
            clients_url = f"{keycloak_url}/admin/realms/{keycloak_realm}/clients"
            query_url = f"{clients_url}?clientId={client_id}"

            async with session.get(query_url, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to query Keycloak clients: {resp.status} - {error_text}")
                    raise RuntimeError("Failed to query Keycloak clients")
                existing_clients = await resp.json()

            if len(existing_clients) > 0:
                DeploymentLogger.log(f"Keycloak client '{client_id}' already exists")
                return

            # Step 3: Create client (matches Ansible keycloak_client_body structure)
            client_body = {
                'clientId': client_id,
                'enabled': True,
                'rootUrl': f'https://{app_host}',
                'baseUrl': f'https://{app_host}',
                'redirectUris': [f'https://{app_host}/*'],
                'webOrigins': [f'https://{app_host}'],
                'publicClient': True,
                'protocol': 'openid-connect'
            }

            async with session.post(clients_url, headers=headers, json=client_body, ssl=False) as resp:
                if resp.status == 201:
                    DeploymentLogger.success(f"Created Keycloak client: {client_id}")
                elif resp.status == 409:
                    DeploymentLogger.log(f"Keycloak client '{client_id}' already exists (409)")
                else:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to create Keycloak client: {resp.status} - {error_text}")
                    raise RuntimeError(f"Failed to create Keycloak client: {resp.status}")

    async def manage_databases(self):
        """Create PostgreSQL databases using asyncpg (DROP then CREATE to match Ansible)."""
        import asyncpg

        admin_password = self._decode_secret_data(self.secrets['admin'], 'admin-password')
        admin_username = self._decode_secret_data(self.secrets['admin'], 'admin-username')
        pg_host = "postgresql-official.postgres.svc.cluster.local"

        try:
            # Connect to 'postgres' database for admin operations (not template1)
            conn = await asyncpg.connect(
                host=pg_host,
                user=admin_username,
                password=admin_password,
                database='postgres'
            )

            for db_name in [f'{self.app_name}_prod', f'{self.app_name}_test', f'test_{self.app_name}']:
                # Terminate active connections to allow DROP (match Ansible clean slate approach)
                await conn.execute("""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = $1 AND pid <> pg_backend_pid()
                """, db_name)

                # DROP then CREATE for clean slate (matches Ansible behavior)
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
                await conn.execute(f'CREATE DATABASE "{db_name}"')
                DeploymentLogger.log(f"Recreated database {db_name}")

            await conn.close()
        except Exception as e:
            DeploymentLogger.error(f"Failed to manage databases: {e}")
            raise

    def _generate_workflow_template(self) -> dict:
        """Generate a WorkflowTemplate using the Jinja2 template from templates/k8s/build-workflow.j2."""
        # Template path - inside container it's at /home/thinkube-control/templates
        template_path = Path("/home/thinkube-control/templates/k8s/build-workflow.j2")

        if not template_path.exists():
            raise FileNotFoundError(f"Workflow template not found: {template_path}")

        # Read the Jinja2 template
        with open(template_path, 'r') as f:
            template_content = f.read()

        # Create Jinja2 environment
        env = jinja2.Environment(
            loader=jinja2.BaseLoader(),
            undefined=jinja2.StrictUndefined
        )
        template = env.from_string(template_content)

        # Get required variables
        system_username = self.params.get('system_username', 'thinkube')
        master_node_name = self.params.get('master_node_name', 'tkspark')
        admin_password = self._decode_secret_data(self.secrets['admin'], 'admin-password')

        # Render template with all required variables (matching Ansible)
        rendered = template.render(
            project_name=self.app_name,
            k8s_namespace=self.namespace,
            master_node_name=master_node_name,
            system_username=system_username,
            container_registry=f"registry.{self.domain}",
            domain_name=self.domain,
            admin_username=self.admin_username,
            admin_password=admin_password,
            thinkube_spec=self.thinkube_config
        )

        # Parse the rendered YAML
        workflow_spec = yaml.safe_load(rendered)

        return workflow_spec

    async def deploy_workflow_template(self):
        """Deploy Argo Workflow template."""
        workflow_file = Path(self.local_repo_path) / 'k8s' / 'build-workflow.yaml'

        if workflow_file.exists():
            # Use custom workflow template from app
            with open(workflow_file, 'r') as f:
                workflow_spec = yaml.safe_load(f)
            DeploymentLogger.log("Using custom workflow template from k8s/build-workflow.yaml")
        else:
            # Generate workflow template from thinkube.yaml
            workflow_spec = self._generate_workflow_template()
            DeploymentLogger.log("Generated workflow template from thinkube.yaml")

        template_name = workflow_spec['metadata']['name']

        try:
            await self.k8s_custom.create_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace="argo",
                plural="workflowtemplates",
                body=workflow_spec
            )
            DeploymentLogger.log(f"Deployed workflow template: {template_name}")
        except ApiException as e:
            if e.status == 409:
                # Get existing template to retrieve resourceVersion
                existing = await self.k8s_custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argo",
                    plural="workflowtemplates",
                    name=template_name
                )
                # Set resourceVersion for update
                workflow_spec['metadata']['resourceVersion'] = existing['metadata']['resourceVersion']

                await self.k8s_custom.replace_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argo",
                    plural="workflowtemplates",
                    name=template_name,
                    body=workflow_spec
                )
                DeploymentLogger.log(f"Updated workflow template: {template_name}")

    # ==================== PHASE 4: Git Operations ====================

    async def phase4_git_operations(self):
        """Phase 4: Sequential git operations + build monitoring."""
        DeploymentLogger.phase(4, "Git Operations & Build Monitoring")

        # Sequential order: create repo → configure webhook → push code
        await self.generate_migrations()
        await self.setup_git_hooks()
        await self.ensure_gitea_repo()
        await self.configure_webhook()

        # Get existing workflow names BEFORE git push so we can detect NEW workflows
        existing_workflows = await self._get_existing_workflow_names()

        await self.git_commit_and_push()

        DeploymentLogger.success("Changes pushed to Gitea")

        # Wait for webhook to trigger workflow (only detect NEW workflows)
        workflow_name = await self.wait_for_workflow_trigger(exclude_workflows=existing_workflows)

        # Monitor workflow until completion
        await self.monitor_workflow(workflow_name)

        DeploymentLogger.success("Phase 4 complete - build succeeded!")

    async def generate_migrations(self):
        """Generate Alembic migrations for containers that need them (matches Ansible)."""
        containers = self.thinkube_config.get('spec', {}).get('containers', [])

        for container in containers:
            migrations = container.get('migrations', {})
            if migrations.get('tool') != 'alembic':
                continue

            build_path = container.get('build', 'backend')
            container_path = Path(self.local_repo_path) / build_path

            # Check if alembic.ini exists
            alembic_ini = container_path / 'alembic.ini'
            if not alembic_ini.exists():
                DeploymentLogger.log(f"No alembic.ini in {build_path}, skipping migrations")
                continue

            admin_username = self._decode_secret_data(self.secrets['admin'], 'admin-username')
            admin_password = self._decode_secret_data(self.secrets['admin'], 'admin-password')
            db_name = self.app_name.replace('-', '_')
            app_host = f"{self.app_name}.{self.domain}"

            # Build migration script (matches Ansible environment setup)
            migration_script = f"""
set -e
cd {container_path}

# Set up database connection
export POSTGRES_USER="{admin_username}"
export POSTGRES_PASSWORD="{admin_password}"
export POSTGRES_HOST="postgres.{self.domain}"
export POSTGRES_PORT="5432"
export POSTGRES_DATABASE="{db_name}"

# Set required application config vars for Pydantic Settings validation
export KEYCLOAK_URL="https://auth.{self.domain}"
export KEYCLOAK_REALM="thinkube"
export KEYCLOAK_CLIENT_ID="{self.namespace}"
export KEYCLOAK_CLIENT_SECRET="dummy"
export FRONTEND_URL="https://{app_host}"

# Set Python path for model imports
export PYTHONPATH="{container_path}:$PYTHONPATH"

# Generate migrations
echo "Generating migrations..."
if alembic revision --autogenerate -m "initial_schema"; then
    echo "Migration generated successfully"
else
    echo "Migration generation failed or no changes detected"
fi
"""

            process = await asyncio.create_subprocess_shell(
                migration_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(container_path)
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                DeploymentLogger.log(f"Generated Alembic migrations for {build_path}")
            else:
                DeploymentLogger.log(f"Migration generation: {stderr.decode().strip()}")

    async def setup_git_hooks(self):
        """Setup git hooks for template processing (matches Ansible setup_git_hooks.yaml)."""
        hooks_dir = Path(self.local_repo_path) / '.git' / 'hooks'
        hooks_dir.mkdir(parents=True, exist_ok=True)

        # Pre-commit hook for Copier template processing
        pre_commit_content = '''#!/bin/bash
# Pre-commit hook to automatically process Copier templates

set -e

# Check if any .jinja files were modified
if ! git diff --cached --name-only | grep -q '\\.jinja$'; then
  exit 0
fi

echo "Processing Copier templates..."

if [ ! -f .copier-answers.yml ]; then
  echo "ERROR: .copier-answers.yml not found."
  exit 1
fi

if command -v copier >/dev/null 2>&1; then
  copier recopy --defaults --overwrite --quiet .
else
  echo "ERROR: Copier not installed."
  exit 1
fi

for jinja_file in $(git diff --cached --name-only | grep '\\.jinja$'); do
  yaml_file="${jinja_file%.jinja}"
  if [ -f "$yaml_file" ]; then
    git add "$yaml_file"
    echo "Added generated file: $yaml_file"
  fi
done

echo "Template processing complete!"
'''

        # Commit-msg hook
        commit_msg_content = '''#!/bin/bash
# Append note if templates were processed

COMMIT_MSG_FILE=$1

if git diff --cached --name-only | grep -q '\\.yaml$' && git diff --cached --name-only | grep -q '\\.jinja$'; then
  echo "" >> "$COMMIT_MSG_FILE"
  echo "[Templates processed by git hook]" >> "$COMMIT_MSG_FILE"
fi
'''

        # Write hooks
        pre_commit_path = hooks_dir / 'pre-commit'
        with open(pre_commit_path, 'w') as f:
            f.write(pre_commit_content)
        pre_commit_path.chmod(0o755)

        commit_msg_path = hooks_dir / 'commit-msg'
        with open(commit_msg_path, 'w') as f:
            f.write(commit_msg_content)
        commit_msg_path.chmod(0o755)

        # Create install-hooks.sh
        install_hooks_content = '''#!/bin/bash
# Reinstall git hooks if needed
echo "Installing git hooks..."
mkdir -p .git/hooks
cp .git-hooks/pre-commit .git/hooks/pre-commit 2>/dev/null || true
chmod +x .git/hooks/pre-commit 2>/dev/null || true
echo "Git hooks installed!"
'''
        install_hooks_path = Path(self.local_repo_path) / 'install-hooks.sh'
        with open(install_hooks_path, 'w') as f:
            f.write(install_hooks_content)
        install_hooks_path.chmod(0o755)

        # Create reprocess-templates.sh
        reprocess_content = f'''#!/bin/bash
# Script to manually reprocess templates
DOMAIN_NAME="{self.domain}"
NAMESPACE="{self.namespace}"

echo "Processing all templates for domain: $DOMAIN_NAME"
for template in $(find . -name "*.jinja" 2>/dev/null); do
    output="${{template%.jinja}}"
    echo "Processing $template -> $output"
    sed -e "s|{{{{ domain_name }}}}|${{DOMAIN_NAME}}|g" \\
        -e "s|{{{{ namespace }}}}|${{NAMESPACE}}|g" \\
        "$template" > "$output"
done
echo "Templates processed!"
'''
        reprocess_path = Path(self.local_repo_path) / 'reprocess-templates.sh'
        with open(reprocess_path, 'w') as f:
            f.write(reprocess_content)
        reprocess_path.chmod(0o755)

        # Create prepare-for-github.sh
        prepare_github_content = '''#!/bin/bash
# Script to prepare repository for pushing to GitHub
echo "Preparing repository for GitHub contribution..."

for template in $(find . -name "*.yaml.jinja" 2>/dev/null); do
    processed="${template%.jinja}"
    if [ -f "$processed" ]; then
        echo "Removing processed file: $processed"
        rm "$processed"
    fi
done

echo "Repository is ready for GitHub contribution"
'''
        prepare_github_path = Path(self.local_repo_path) / 'prepare-for-github.sh'
        with open(prepare_github_path, 'w') as f:
            f.write(prepare_github_content)
        prepare_github_path.chmod(0o755)

        # Create DEVELOPMENT.md
        development_md_content = f'''# Development Workflow

This repository is hosted on Gitea for local development with Thinkube.

## Initial Setup

Install git hooks for automatic template processing:
```bash
./install-hooks.sh
```

## Making Changes

1. **Edit templates** (`.yaml.jinja` files), not processed files
2. **Commit your changes** - templates are automatically processed!
   ```bash
   git add .
   git commit -m "Update deployment configuration"
   ```

## Manual Template Processing

```bash
./reprocess-templates.sh
```

## Contributing to GitHub

1. **Prepare for GitHub** (removes processed files):
   ```bash
   ./prepare-for-github.sh
   ```

2. **Push to GitHub** and create PR
'''
        dev_md_path = Path(self.local_repo_path) / 'DEVELOPMENT.md'
        with open(dev_md_path, 'w') as f:
            f.write(development_md_content)

        DeploymentLogger.log("Setup git hooks and helper scripts")

    async def configure_webhook(self):
        """Configure Gitea webhook (atomic operation - prevents duplicates)."""
        gitea_token = self._decode_secret_data(self.secrets['gitea'], 'token')
        gitea_hostname = f"git.{self.domain}"
        webhook_url = f"https://argo-events.{self.domain}/gitea"
        org = "thinkube-deployments"
        repo = self.app_name

        # Get webhook secret from Argo namespace
        try:
            webhook_secret_obj = await self.k8s_core.read_namespaced_secret('gitea-webhook-secret', 'argo')
            webhook_secret = self._decode_secret_data(webhook_secret_obj, 'secret')
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get webhook secret: {e}")
            raise

        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'token {gitea_token}',
                'Content-Type': 'application/json'
            }

            # 1. Get all existing webhooks
            hooks_url = f"https://{gitea_hostname}/api/v1/repos/{org}/{repo}/hooks"
            async with session.get(hooks_url, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    DeploymentLogger.error(f"Failed to get webhooks: {resp.status}")
                    raise RuntimeError(f"Failed to fetch existing webhooks: {resp.status}")
                existing_hooks = await resp.json()

            # 2. Check if webhook already exists (match Ansible behavior)
            webhook_exists = any(
                hook.get('config', {}).get('url') == webhook_url
                for hook in existing_hooks
            )

            if webhook_exists:
                DeploymentLogger.log(f"Webhook already configured for {org}/{repo}")
            else:
                # 3. Create webhook only if it doesn't exist
                webhook_payload = {
                    'type': 'gitea',
                    'config': {
                        'url': webhook_url,
                        'content_type': 'json',
                        'secret': webhook_secret
                    },
                    'events': ['push'],
                    'active': True
                }

                async with session.post(hooks_url, headers=headers, json=webhook_payload, ssl=False) as resp:
                    if resp.status == 201:
                        webhook_data = await resp.json()
                        webhook_id = webhook_data['id']
                        DeploymentLogger.success(f"Created webhook ID {webhook_id} for {org}/{repo}")
                    else:
                        error_text = await resp.text()
                        DeploymentLogger.error(f"Failed to create webhook: {resp.status} - {error_text}")
                        raise RuntimeError(f"Failed to create webhook: {resp.status}")

    async def git_commit_and_push(self):
        """Commit and push changes to Gitea."""
        gitea_token = self._decode_secret_data(self.secrets['gitea'], 'token')
        gitea_hostname = f"git.{self.domain}"
        org = "thinkube-deployments"

        # Match Ansible: only init if .git doesn't exist, preserve existing history
        git_script = f"""
set -e
cd {self.local_repo_path}
if [ ! -d .git ]; then
  git init -b main
fi
git config user.name '{self.admin_username}'
git config user.email '{self.admin_username}@{self.domain}'
git remote remove origin 2>/dev/null || true
git remote add origin 'https://{self.admin_username}:{gitea_token}@{gitea_hostname}/{org}/{self.app_name}.git'
git add -A
git commit --allow-empty -m 'Deploy {self.app_name} to {self.domain}'
git push -u origin main --force
"""

        process = await asyncio.create_subprocess_shell(
            git_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.local_repo_path
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            DeploymentLogger.error(f"Git operations failed")
            DeploymentLogger.error(f"Error: {stderr.decode()}")
            raise RuntimeError(f"Git operations failed: {stderr.decode()}")

        DeploymentLogger.success("Pushed changes to Gitea")

    async def _get_existing_workflow_names(self) -> set:
        """Get set of existing workflow names for this app."""
        try:
            workflows = await self.k8s_custom.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace="argo",
                plural="workflows",
                label_selector=f"thinkube.io/app-name={self.app_name}"
            )
            return {item['metadata']['name'] for item in workflows.get('items', [])}
        except ApiException:
            return set()

    async def wait_for_workflow_trigger(self, timeout: int = 60, exclude_workflows: set = None) -> str:
        """Wait for webhook to trigger a NEW Argo Workflow."""
        DeploymentLogger.log("Waiting for webhook to trigger build workflow...")

        if exclude_workflows is None:
            exclude_workflows = set()

        start_time = asyncio.get_event_loop().time()
        workflow_name = None

        while True:
            try:
                # List workflows with label selector for this app
                workflows = await self.k8s_custom.list_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argo",
                    plural="workflows",
                    label_selector=f"thinkube.io/app-name={self.app_name}"
                )

                # Find a NEW workflow (not in exclude list)
                items = workflows.get('items', [])
                new_workflows = [w for w in items if w['metadata']['name'] not in exclude_workflows]

                if new_workflows:
                    # Sort by creation timestamp, get latest
                    new_workflows.sort(key=lambda x: x['metadata']['creationTimestamp'], reverse=True)
                    latest = new_workflows[0]
                    workflow_name = latest['metadata']['name']

                    DeploymentLogger.success(f"Workflow triggered: {workflow_name}")
                    break

            except ApiException as e:
                if e.status != 404:
                    DeploymentLogger.error(f"Error checking for workflow: {e}")

            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                DeploymentLogger.error(f"Timeout waiting for workflow to trigger after {timeout}s")
                raise TimeoutError("Workflow was not triggered within timeout period")

            await asyncio.sleep(2)

        return workflow_name

    async def monitor_workflow(self, workflow_name: str):
        """Monitor Argo Workflow execution and stream status."""
        argo_ui_url = f"https://argo.{self.domain}/workflows/argo/{workflow_name}"
        DeploymentLogger.log(f"🔗 Argo Workflow UI: {argo_ui_url}")

        last_reported_nodes = set()

        while True:
            try:
                workflow = await self.k8s_custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argo",
                    plural="workflows",
                    name=workflow_name
                )

                status = workflow.get('status', {})
                phase = status.get('phase')
                nodes = status.get('nodes', {})

                # Report new node statuses
                for node_id, node in nodes.items():
                    node_name = node.get('displayName', node.get('name', 'unknown'))
                    node_phase = node.get('phase')
                    node_key = f"{node_name}:{node_phase}"

                    if node_key not in last_reported_nodes:
                        last_reported_nodes.add(node_key)

                        if node_phase == 'Running':
                            DeploymentLogger.log(f"  ⚙️  {node_name}: Running")
                        elif node_phase == 'Succeeded':
                            DeploymentLogger.log(f"  ✅ {node_name}: Succeeded")
                        elif node_phase in ['Failed', 'Error']:
                            DeploymentLogger.error(f"  ❌ {node_name}: {node_phase}")

                # Check overall workflow status
                if phase == 'Succeeded':
                    DeploymentLogger.success("🎉 Build workflow completed successfully!")
                    DeploymentLogger.log(f"🔗 View details: {argo_ui_url}")
                    break

                elif phase in ['Failed', 'Error']:
                    message = status.get('message', 'No error message')
                    DeploymentLogger.error(f"❌ Build workflow failed: {message}")
                    DeploymentLogger.error(f"🔗 View failure details: {argo_ui_url}")
                    raise RuntimeError(f"Workflow {workflow_name} failed: {message}")

                elif phase in ['Pending', 'Running']:
                    # Still running, continue monitoring
                    pass

                else:
                    DeploymentLogger.log(f"Workflow status: {phase}")

            except ApiException as e:
                if e.status == 404:
                    DeploymentLogger.error(f"Workflow {workflow_name} not found")
                    raise
                else:
                    DeploymentLogger.error(f"Error monitoring workflow: {e}")

            await asyncio.sleep(5)

    # ==================== PHASE 5: Deployment ====================

    async def phase5_deploy(self):
        """Phase 5: ArgoCD deployment and service discovery."""
        DeploymentLogger.phase(5, "Deployment & Service Discovery")

        await self.deploy_argocd_app()
        await self.configure_cicd_monitoring()
        await self.setup_service_discovery()

        DeploymentLogger.success("Phase 5 complete - application deployed")

    async def deploy_argocd_app(self):
        """Create ArgoCD application with SSH setup (matches Ansible argocd role)."""
        gitea_hostname = f"git.{self.domain}"
        argocd_namespace = "argocd"

        # Step 1: Get gitea-ssh-key from argo namespace
        try:
            gitea_ssh_secret = await self.k8s_core.read_namespaced_secret('gitea-ssh-key', 'argo')
            ssh_private_key = self._decode_secret_data(gitea_ssh_secret, 'ssh-privatekey')
        except ApiException as e:
            DeploymentLogger.error(f"Failed to get gitea-ssh-key from argo namespace: {e}")
            raise

        # Step 2: Create SSH repository secret for ArgoCD
        ssh_secret_name = f"gitea-{self.app_name}-ssh"
        ssh_repo_url = f"ssh://git@{gitea_hostname}:2222/thinkube-deployments/{self.app_name}.git"

        ssh_secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=ssh_secret_name,
                namespace=argocd_namespace,
                labels={'argocd.argoproj.io/secret-type': 'repository'}
            ),
            string_data={
                'url': ssh_repo_url,
                'sshPrivateKey': ssh_private_key,
                'type': 'git'
            }
        )

        try:
            await self.k8s_core.create_namespaced_secret(argocd_namespace, ssh_secret)
            DeploymentLogger.log(f"Created SSH secret: {ssh_secret_name}")
        except ApiException as e:
            if e.status == 409:
                await self.k8s_core.replace_namespaced_secret(ssh_secret_name, argocd_namespace, ssh_secret)
                DeploymentLogger.log(f"Updated SSH secret: {ssh_secret_name}")

        # Step 3: Restart argocd-repo-server to pick up SSH config
        try:
            # Patch the deployment to trigger a restart (update an annotation)
            patch_body = {
                'spec': {
                    'template': {
                        'metadata': {
                            'annotations': {
                                'kubectl.kubernetes.io/restartedAt': datetime.now().isoformat()
                            }
                        }
                    }
                }
            }
            await self.k8s_apps.patch_namespaced_deployment(
                name='argocd-repo-server',
                namespace=argocd_namespace,
                body=patch_body
            )
            DeploymentLogger.log("Restarted argocd-repo-server to pick up SSH config")

            # Wait briefly for restart to begin
            await asyncio.sleep(5)
        except ApiException as e:
            DeploymentLogger.error(f"Failed to restart argocd-repo-server: {e}")

        # Step 4: Create ArgoCD Application with SSH URL
        argocd_app = {
            'apiVersion': 'argoproj.io/v1alpha1',
            'kind': 'Application',
            'metadata': {
                'name': self.app_name,
                'namespace': argocd_namespace
            },
            'spec': {
                'project': 'default',
                'source': {
                    'repoURL': ssh_repo_url,
                    'targetRevision': 'HEAD',
                    'path': 'k8s'
                },
                'destination': {
                    'server': 'https://kubernetes.default.svc',
                    'namespace': self.namespace
                },
                'syncPolicy': {
                    'syncOptions': ['CreateNamespace=true']
                },
                'revisionHistoryLimit': 3
            }
        }

        try:
            await self.k8s_custom.create_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=argocd_namespace,
                plural="applications",
                body=argocd_app
            )
            DeploymentLogger.success(f"Created ArgoCD application: {self.app_name}")
        except ApiException as e:
            if e.status == 409:
                # Get existing to preserve resourceVersion
                existing = await self.k8s_custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=argocd_namespace,
                    plural="applications",
                    name=self.app_name
                )
                argocd_app['metadata']['resourceVersion'] = existing['metadata']['resourceVersion']
                await self.k8s_custom.replace_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=argocd_namespace,
                    plural="applications",
                    name=self.app_name,
                    body=argocd_app
                )
                DeploymentLogger.log("Updated ArgoCD application")

    async def configure_cicd_monitoring(self):
        """Register repository with CI/CD monitoring API (matches Ansible)."""
        # CI/CD monitoring webhook is optional
        if not self.thinkube_config.get('cicd', {}).get('enable_monitoring', True):
            DeploymentLogger.log("CI/CD monitoring disabled, skipping")
            return

        cicd_token = self._decode_secret_data(self.secrets['cicd_token'], 'token')
        control_url = f"https://control.{self.domain}/api/v1/cicd/repositories"

        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {cicd_token}',
                'Content-Type': 'application/json'
            }

            body = {
                'repository_url': f"https://git.{self.domain}/thinkube-deployments/{self.app_name}",
                'repository_name': self.app_name,
                'active': True
            }

            async with session.post(control_url, headers=headers, json=body, ssl=False) as resp:
                if resp.status in [200, 201]:
                    DeploymentLogger.success(f"Registered {self.app_name} with CI/CD monitoring")
                elif resp.status == 409:
                    DeploymentLogger.log("Repository already registered with CI/CD monitoring")
                else:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to register with CI/CD monitoring: {resp.status} - {error_text}")

    async def setup_service_discovery(self):
        """Setup service discovery via thinkube-control API (matches Ansible)."""
        cicd_token = self._decode_secret_data(self.secrets['cicd_token'], 'token')
        control_base = f"https://control.{self.domain}"
        app_host = f"{self.app_name}.{self.domain}"

        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {cicd_token}',
                'Content-Type': 'application/json'
            }

            # Step 1: Generate service discovery YAML via API
            generate_url = f"{control_base}/api/v1/config/service-discovery/generate-configmap-yaml"
            body = {
                'app_name': self.app_name,
                'app_host': app_host,
                'k8s_namespace': self.namespace,
                'template_url': self.template_url,
                'project_description': self.params.get('project_description', ''),
                'deployment_date': datetime.now().isoformat(),
                'containers': self.thinkube_config.get('spec', {}).get('containers', [])
            }

            yaml_content = None
            async with session.post(generate_url, headers=headers, json=body, ssl=False) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    yaml_content = result.get('yaml_content', '')
                    DeploymentLogger.log("Generated service discovery YAML")
                else:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to generate service discovery YAML: {resp.status} - {error_text}")

            # Step 2: Create ConfigMap with the generated YAML
            if yaml_content:
                discovery_cm = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(
                        name='thinkube-service-config',
                        namespace=self.namespace,
                        labels={
                            'app': self.app_name,
                            'thinkube.io/managed': 'true',
                            'thinkube.io/service-type': 'user_app',
                            'thinkube.io/service-name': self.app_name
                        }
                    ),
                    data={'service.yaml': yaml_content}
                )

                try:
                    await self.k8s_core.create_namespaced_config_map(self.namespace, discovery_cm)
                    DeploymentLogger.log("Created thinkube-service-config ConfigMap")
                except ApiException as e:
                    if e.status == 409:
                        await self.k8s_core.replace_namespaced_config_map(
                            'thinkube-service-config', self.namespace, discovery_cm
                        )
                        DeploymentLogger.log("Updated thinkube-service-config ConfigMap")

            # Step 3: Trigger service sync to register app immediately
            sync_url = f"{control_base}/api/v1/services/sync"
            async with session.post(sync_url, headers=headers, ssl=False) as resp:
                if resp.status in [200, 201]:
                    DeploymentLogger.success("Service discovery sync triggered - app registered")
                else:
                    DeploymentLogger.log("Service sync trigger failed (app will appear via auto-discovery within 5 min)")

    # ==================== Main Orchestration ====================

    async def deploy(self):
        """Main deployment orchestration."""
        start_time = datetime.now()
        DeploymentLogger.log(f"Starting deployment of {self.app_name}")

        try:
            await self.initialize_k8s_clients()

            await self.phase1_setup()
            await self.phase2_gather_resources()
            await self.phase3_create_resources()
            await self.phase4_git_operations()
            await self.phase5_deploy()

            elapsed = (datetime.now() - start_time).total_seconds()
            DeploymentLogger.success(f"Deployment complete in {elapsed:.1f} seconds")

            return 0

        except Exception as e:
            DeploymentLogger.error(f"Deployment failed: {e}")
            import traceback
            traceback.print_exc()
            return 1
        finally:
            await self.cleanup_k8s_clients()


async def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: deploy_application.py <params_json_file>", file=sys.stderr)
        sys.exit(1)

    params_file = sys.argv[1]

    try:
        with open(params_file, 'r') as f:
            params = json.load(f)
    except Exception as e:
        DeploymentLogger.error(f"Failed to load parameters: {e}")
        sys.exit(1)

    deployer = ApplicationDeployer(params)
    exit_code = await deployer.deploy()
    sys.exit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())
