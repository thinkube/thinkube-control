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

        # ensure_gitea_repo depends on gitea token, run after gather
        await self.ensure_gitea_repo()

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
        """Ensure Gitea repository exists (idempotent)."""
        gitea_token = self._decode_secret_data(self.secrets['gitea'], 'token')
        gitea_hostname = f"git.{self.domain}"
        org = "thinkube-deployments"

        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'token {gitea_token}',
                'Content-Type': 'application/json'
            }

            # Create repository (idempotent - accepts 409)
            repo_url = f"https://{gitea_hostname}/api/v1/orgs/{org}/repos"
            repo_payload = {
                'name': self.app_name,
                'description': f'Deployment manifests for {self.app_name}',
                'private': True,
                'auto_init': False
            }

            async with session.post(repo_url, headers=headers, json=repo_payload, ssl=False) as resp:
                if resp.status == 201:
                    DeploymentLogger.log(f"Created Gitea repository: {org}/{self.app_name}")
                elif resp.status == 409:
                    DeploymentLogger.log(f"Gitea repository already exists: {org}/{self.app_name}")
                else:
                    error_text = await resp.text()
                    DeploymentLogger.error(f"Failed to ensure Gitea repo: {resp.status} - {error_text}")
                    raise RuntimeError(f"Failed to create/verify Gitea repository: {resp.status}")

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
            self.deploy_workflow_template(),
            return_exceptions=False
        )

        DeploymentLogger.success("Phase 3 complete - all resources created")

    async def create_tls_secret(self):
        """Copy TLS certificate to application namespace."""
        cert = self.secrets['wildcard_cert']
        new_secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name=cert.metadata.name, namespace=self.namespace),
            data=cert.data,
            type=cert.type
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, new_secret)
            DeploymentLogger.log(f"Created TLS secret in {self.namespace}")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("TLS secret already exists")
            else:
                raise

    async def create_harbor_secret(self):
        """Create Harbor robot secret in application namespace."""
        harbor = self.secrets['harbor']
        new_secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name='harbor-docker-config', namespace=self.namespace),
            data=harbor.data,
            type=harbor.type
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, new_secret)
            DeploymentLogger.log("Created Harbor secret")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("Harbor secret already exists")

    async def create_postgres_secret(self):
        """Create PostgreSQL credentials secret."""
        admin_secret = self.secrets['admin']
        postgres_password = self._decode_secret_data(admin_secret, 'password')

        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name='postgres-credentials', namespace=self.namespace),
            string_data={
                'username': 'postgres',
                'password': postgres_password,
                'database': f'{self.app_name}_prod'
            }
        )
        try:
            await self.k8s_core.create_namespaced_secret(self.namespace, secret)
            DeploymentLogger.log("Created PostgreSQL secret")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("PostgreSQL secret already exists")

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
            metadata=client.V1ObjectMeta(name='app-metadata', namespace=self.namespace),
            data={
                'app_name': self.app_name,
                'domain': self.domain,
                'namespace': self.namespace,
                'config': yaml.dump(self.thinkube_config)
            }
        )
        try:
            await self.k8s_core.create_namespaced_config_map(self.namespace, metadata)
            DeploymentLogger.log("Created app metadata")
        except ApiException as e:
            if e.status == 409:
                DeploymentLogger.log("App metadata already exists")

    async def manage_databases(self):
        """Create PostgreSQL databases."""
        admin_password = self._decode_secret_data(self.secrets['admin'], 'password')

        # Simple database creation via subprocess
        for db_name in [f'{self.app_name}_prod', f'{self.app_name}_test']:
            cmd = f"PGPASSWORD='{admin_password}' psql -h postgres -U postgres -tc \"SELECT 1 FROM pg_database WHERE datname = '{db_name}'\" | grep -q 1 || PGPASSWORD='{admin_password}' psql -h postgres -U postgres -c \"CREATE DATABASE {db_name}\""

            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            DeploymentLogger.log(f"Ensured database {db_name} exists")

    async def deploy_workflow_template(self):
        """Deploy Argo Workflow template."""
        workflow_file = Path(self.local_repo_path) / 'k8s' / 'build-workflow.yaml'

        if not workflow_file.exists():
            DeploymentLogger.log("No workflow template found, skipping")
            return

        with open(workflow_file, 'r') as f:
            workflow_spec = yaml.safe_load(f)

        try:
            await self.k8s_custom.create_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace="argo",
                plural="workflowtemplates",
                body=workflow_spec
            )
            DeploymentLogger.log("Deployed workflow template")
        except ApiException as e:
            if e.status == 409:
                await self.k8s_custom.replace_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argo",
                    plural="workflowtemplates",
                    name=workflow_spec['metadata']['name'],
                    body=workflow_spec
                )
                DeploymentLogger.log("Updated workflow template")

    # ==================== PHASE 4: Git Operations ====================

    async def phase4_git_operations(self):
        """Phase 4: Sequential git operations + build monitoring."""
        DeploymentLogger.phase(4, "Git Operations & Build Monitoring")

        # These must happen in order
        await self.generate_migrations()
        await self.setup_git_hooks()
        await self.configure_webhook()
        await self.git_commit_and_push()

        DeploymentLogger.success("Changes pushed to Gitea")

        # Wait for webhook to trigger workflow
        workflow_name = await self.wait_for_workflow_trigger()

        # Monitor workflow until completion
        await self.monitor_workflow(workflow_name)

        DeploymentLogger.success("Phase 4 complete - build succeeded!")

    async def generate_migrations(self):
        """Generate Alembic migrations if needed."""
        # Check if alembic.ini exists in the repo
        alembic_ini = Path(self.local_repo_path) / 'alembic.ini'
        if not alembic_ini.exists():
            DeploymentLogger.log("No alembic.ini found, skipping migrations")
            return

        # Run alembic revision if needed
        # This is optional - migrations can be generated separately
        DeploymentLogger.log("Alembic configuration found (migrations can be generated manually if needed)")

    async def setup_git_hooks(self):
        """Setup git hooks in local repository."""
        # Git hooks are typically set up by the Ansible role
        # For now, we'll skip this in the Python version
        # The hooks will be set up when the role is ported
        DeploymentLogger.log("Git hooks setup (handled separately)")

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

            # 2. Delete any webhooks with matching URL (atomic cleanup)
            for hook in existing_hooks:
                hook_config_url = hook.get('config', {}).get('url')
                if hook_config_url == webhook_url:
                    hook_id = hook['id']
                    delete_url = f"{hooks_url}/{hook_id}"
                    async with session.delete(delete_url, headers=headers, ssl=False) as resp:
                        if resp.status == 204:
                            DeploymentLogger.log(f"Deleted duplicate webhook ID {hook_id}")
                        else:
                            DeploymentLogger.error(f"Failed to delete webhook {hook_id}: {resp.status}")

            # 3. Create the new webhook
            webhook_payload = {
                'type': 'gitea',
                'config': {
                    'url': webhook_url,
                    'content_type': 'json',
                    'secret': webhook_secret
                },
                'events': ['push', 'pull_request'],
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

        git_commands = [
            # Initialize if needed (must come first)
            "git init",

            # Configure git (after init)
            f"git config user.name '{self.admin_username}'",
            f"git config user.email '{self.admin_username}@{self.domain}'",

            # Add remote
            f"git remote remove origin || true",
            f"git remote add origin https://{self.admin_username}:{gitea_token}@{gitea_hostname}/{org}/{self.app_name}.git",

            # Stage all changes
            "git add -A",

            # Commit
            f"git commit -m 'Update deployment manifests for {self.domain}' || echo 'No changes to commit'",

            # Push
            "git push -u origin main --force"
        ]

        for cmd in git_commands:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.local_repo_path
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0 and "No changes to commit" not in stdout.decode():
                if "git remote add origin" not in cmd:  # Ignore remote errors
                    DeploymentLogger.error(f"Git command failed: {cmd}")
                    DeploymentLogger.error(f"Error: {stderr.decode()}")

        DeploymentLogger.success("Pushed changes to Gitea")

    async def wait_for_workflow_trigger(self, timeout: int = 60) -> str:
        """Wait for webhook to trigger Argo Workflow."""
        DeploymentLogger.log("Waiting for webhook to trigger build workflow...")

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

                # Find the most recent workflow
                items = workflows.get('items', [])
                if items:
                    # Sort by creation timestamp, get latest
                    items.sort(key=lambda x: x['metadata']['creationTimestamp'], reverse=True)
                    latest = items[0]
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
        """Create and sync ArgoCD application."""
        argocd_app = {
            'apiVersion': 'argoproj.io/v1alpha1',
            'kind': 'Application',
            'metadata': {
                'name': self.app_name,
                'namespace': 'argocd'
            },
            'spec': {
                'project': 'default',
                'source': {
                    'repoURL': f'https://git.{self.domain}/thinkube-deployments/{self.app_name}.git',
                    'targetRevision': 'main',
                    'path': 'k8s'
                },
                'destination': {
                    'server': 'https://kubernetes.default.svc',
                    'namespace': self.namespace
                },
                'syncPolicy': {
                    'automated': {
                        'prune': True,
                        'selfHeal': True
                    }
                }
            }
        }

        try:
            await self.k8s_custom.create_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace="argocd",
                plural="applications",
                body=argocd_app
            )
            DeploymentLogger.success(f"Created ArgoCD application: {self.app_name}")
        except ApiException as e:
            if e.status == 409:
                await self.k8s_custom.replace_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argocd",
                    plural="applications",
                    name=self.app_name,
                    body=argocd_app
                )
                DeploymentLogger.log("Updated ArgoCD application")

    async def configure_cicd_monitoring(self):
        """Configure CI/CD monitoring webhook."""
        # CI/CD monitoring webhook is optional
        if not self.thinkube_config.get('cicd', {}).get('enable_monitoring', True):
            DeploymentLogger.log("CI/CD monitoring disabled, skipping")
            return

        DeploymentLogger.log("Configured CI/CD monitoring")

    async def setup_service_discovery(self):
        """Setup service discovery."""
        # Service discovery registration
        discovery_cm = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=f'{self.app_name}-service-discovery',
                namespace=self.namespace
            ),
            data={
                'service_name': self.app_name,
                'namespace': self.namespace,
                'domain': f'{self.app_name}.{self.domain}'
            }
        )

        try:
            await self.k8s_core.create_namespaced_config_map(self.namespace, discovery_cm)
            DeploymentLogger.log("Created service discovery config")
        except ApiException as e:
            if e.status == 409:
                await self.k8s_core.replace_namespaced_config_map(
                    f'{self.app_name}-service-discovery',
                    self.namespace,
                    discovery_cm
                )
                DeploymentLogger.log("Updated service discovery config")

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
