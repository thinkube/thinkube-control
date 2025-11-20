"""
Model Downloader Service

Manages HuggingFace model downloads to thinkube-models PVC using Argo Workflows via Hera.
"""

import os
import logging
from typing import List, Dict, Optional
from datetime import datetime

from hera.workflows import Workflow, Container, models as hera_models, WorkflowsService
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from mlflow.tracking import MlflowClient
from mlflow.exceptions import RestException

logger = logging.getLogger(__name__)

# Hardcoded model catalog for v1 (matches tkt-tensorrt-llm manifest)
AVAILABLE_MODELS = [
    {
        "id": "openai/gpt-oss-20b",
        "name": "GPT-OSS 20B",
        "size": "~20GB",
        "quantization": "FP8",
        "description": "OpenAI GPT-OSS 20B model optimized for TensorRT-LLM",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "openai/gpt-oss-120b",
        "name": "GPT-OSS 120B",
        "size": "~70GB",
        "quantization": "FP4",
        "description": "OpenAI GPT-OSS 120B model optimized for TensorRT-LLM",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Llama-3.1-8B-Instruct-FP8",
        "name": "Llama 3.1 8B Instruct",
        "size": "~8GB",
        "quantization": "FP8",
        "description": "Meta Llama 3.1 8B instruction-tuned model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Llama-3.1-8B-Instruct-FP4",
        "name": "Llama 3.1 8B Instruct",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Meta Llama 3.1 8B instruction-tuned model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Llama-3.3-70B-Instruct-FP4",
        "name": "Llama 3.3 70B Instruct",
        "size": "~35GB",
        "quantization": "FP4",
        "description": "Meta Llama 3.3 70B instruction-tuned model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-8B-FP8",
        "name": "Qwen3 8B",
        "size": "~8GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen3 8B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-8B-FP4",
        "name": "Qwen3 8B",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 8B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-14B-FP8",
        "name": "Qwen3 14B",
        "size": "~14GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen3 14B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-14B-FP4",
        "name": "Qwen3 14B",
        "size": "~7GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 14B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-32B-FP4",
        "name": "Qwen3 32B",
        "size": "~16GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 32B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Phi-4-multimodal-instruct-FP8",
        "name": "Phi-4 Multimodal Instruct",
        "size": "~6GB",
        "quantization": "FP8",
        "description": "Microsoft Phi-4 multimodal instruction-tuned model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Phi-4-multimodal-instruct-FP4",
        "name": "Phi-4 Multimodal Instruct",
        "size": "~3GB",
        "quantization": "FP4",
        "description": "Microsoft Phi-4 multimodal instruction-tuned model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Phi-4-reasoning-plus-FP8",
        "name": "Phi-4 Reasoning Plus",
        "size": "~6GB",
        "quantization": "FP8",
        "description": "Microsoft Phi-4 reasoning-focused model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Phi-4-reasoning-plus-FP4",
        "name": "Phi-4 Reasoning Plus",
        "size": "~3GB",
        "quantization": "FP4",
        "description": "Microsoft Phi-4 reasoning-focused model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Llama-3_3-Nemotron-Super-49B-v1_5-FP8",
        "name": "Llama 3.3 Nemotron Super 49B",
        "size": "~49GB",
        "quantization": "FP8",
        "description": "NVIDIA Nemotron variant of Llama 3.3",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-30B-A3B-FP4",
        "name": "Qwen3 30B-A3B",
        "size": "~15GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 30B-A3B model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen2.5-VL-7B-Instruct-FP8",
        "name": "Qwen2.5 VL 7B Instruct",
        "size": "~7GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen2.5 Vision-Language model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen2.5-VL-7B-Instruct-FP4",
        "name": "Qwen2.5 VL 7B Instruct",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen2.5 Vision-Language model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Llama-4-Scout-17B-16E-Instruct-FP4",
        "name": "Llama 4 Scout 17B-16E Instruct",
        "size": "~9GB",
        "quantization": "FP4",
        "description": "Meta Llama 4 Scout model",
        "server_type": ["tensorrt-llm"],
        "task": "text-generation"
    },
    {
        "id": "nvidia/Qwen3-235B-A22B-FP4",
        "name": "Qwen3 235B-A22B",
        "size": "~120GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 235B-A22B large model",
        "server_type": ["tensorrt-llm"]
    }
]


class ModelDownloaderService:
    """Service for managing model downloads via Argo Workflows"""

    def __init__(self):
        """Initialize the model downloader service"""
        # Load in-cluster Kubernetes config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            # Fallback to kubeconfig for local development
            config.load_kube_config()

        self.core_v1 = client.CoreV1Api()
        self.custom_api = client.CustomObjectsApi()

        # Configuration
        self.workflow_namespace = "argo"  # Run workflows in argo namespace
        self.models_pvc_name = "thinkube-models"
        self.parallelism = 3  # Max concurrent downloads

        # Get MLflow URI from environment
        self.mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")

        # Configure Hera workflows service for in-cluster Argo Workflows access
        # Read service account token for authentication
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        try:
            with open(token_path, "r") as f:
                token = f.read().strip()
        except Exception as e:
            logger.warning(f"Could not read service account token: {e}")
            token = None

        self.workflows_service = WorkflowsService(
            host="http://argo-workflows-server.argo.svc.cluster.local:2746",
            verify_ssl=False,
            token=token,
            namespace=self.workflow_namespace
        )

    def get_available_models(self) -> List[Dict]:
        """
        Get list of available models for download

        Returns:
            List of model dictionaries with metadata
        """
        return AVAILABLE_MODELS.copy()

    def submit_download(self, model_id: str) -> str:
        """
        Submit a model download workflow to Argo

        Args:
            model_id: HuggingFace model ID (e.g., "nvidia/Phi-4-multimodal-instruct-FP8")

        Returns:
            Workflow name/ID

        Raises:
            ValueError: If model_id not in catalog
            ApiException: If workflow submission fails

        Note:
            HuggingFace token is read from the 'huggingface-token' k8s secret in argo namespace
        """
        # Validate model exists in catalog and get task
        model_info = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)
        if not model_info:
            raise ValueError(f"Model '{model_id}' not found in catalog")

        model_task = model_info.get("task", "text-generation")  # Default to text-generation
        logger.info(f"Creating download workflow for model: {model_id} (task: {model_task})")

        # Escape for safe use in Python script
        safe_model_id = model_id.replace("'", "\\'")
        safe_model_task = model_task.replace("'", "\\'")

        # Create Hera workflow
        with Workflow(
            generate_name="model-dl-",
            namespace=self.workflow_namespace,
            workflows_service=self.workflows_service,
            service_account_name="thinkube-control",
            entrypoint="download",
            parallelism=self.parallelism,
            labels={
                "model-id": model_id.replace("/", "-"),  # Label for tracking which model
                "workflow-type": "model-download"
            },
            retry_strategy=hera_models.RetryStrategy(
                limit=10,
                retry_policy="OnFailure",
                backoff=hera_models.Backoff(
                    duration="1m",
                    factor=2,
                    max_duration="10m"
                )
            ),
            volumes=[
                hera_models.Volume(
                    name="download-cache",
                    empty_dir={"sizeLimit": "200Gi"}  # Local host disk for temporary downloads
                )
            ]
        ) as w:
            # Download script with S3 upload and MLflow registration
            download_script = f"""
import os
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
import mlflow
import boto3
from botocore.config import Config

# Force progress bars to show even without TTY
os.environ['TQDM_DISABLE'] = '0'
os.environ['TQDM_MININTERVAL'] = '10'

# Get MLflow authentication token from Keycloak
print('Authenticating with MLflow...', flush=True)
import requests
token_url = os.environ['MLFLOW_KEYCLOAK_TOKEN_URL']
client_id = os.environ['MLFLOW_KEYCLOAK_CLIENT_ID']
client_secret = os.environ['MLFLOW_CLIENT_SECRET']
username = os.environ['MLFLOW_AUTH_USERNAME']
password = os.environ['MLFLOW_AUTH_PASSWORD']

token_response = requests.post(
    token_url,
    data={{
        'grant_type': 'password',
        'client_id': client_id,
        'client_secret': client_secret,
        'username': username,
        'password': password
    }},
    verify=False  # Skip SSL verification for internal cluster communication
)
token_response.raise_for_status()
os.environ['MLFLOW_TRACKING_TOKEN'] = token_response.json()['access_token']
print('✓ MLflow authentication successful', flush=True)

# Get MLflow URI from environment
mlflow_uri = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow.mlflow.svc.cluster.local:5000')
mlflow.set_tracking_uri(mlflow_uri)

model_id = '{safe_model_id}'
model_task = '{safe_model_task}'
print(f'Starting download of {{model_id}}...', flush=True)

# Configure S3 client for SeaweedFS
s3_endpoint = os.environ['AWS_S3_ENDPOINT']
s3_access_key = os.environ['AWS_ACCESS_KEY_ID']
s3_secret_key = os.environ['AWS_SECRET_ACCESS_KEY']

s3_client = boto3.client(
    's3',
    endpoint_url=s3_endpoint,
    aws_access_key_id=s3_access_key,
    aws_secret_access_key=s3_secret_key,
    config=Config(signature_version='s3v4'),
    verify=False  # Skip SSL for internal cluster communication
)

try:
    model_name = model_id.replace('/', '-')
    s3_bucket = 'mlflow'
    # Store in MLflow's artifact root path so artifacts are visible in UI
    s3_prefix = f'artifacts/models/{{model_name}}'

    # Check if model already exists in S3
    print(f'Checking if model exists in S3: s3://{{s3_bucket}}/{{s3_prefix}}/', flush=True)
    try:
        response = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix=s3_prefix, MaxKeys=1)
        if response.get('KeyCount', 0) > 0:
            print(f'✓ Model already exists in S3, skipping download', flush=True)
            model_already_exists = True
        else:
            model_already_exists = False
    except Exception as list_err:
        print(f'Could not check S3 (assuming model does not exist): {{list_err}}', flush=True)
        model_already_exists = False

    # Download to temporary local storage
    temp_download_dir = '/tmp/downloads'
    os.makedirs(temp_download_dir, exist_ok=True)
    temp_model_path = os.path.join(temp_download_dir, model_name)

    if not model_already_exists:
        print('Downloading model files from HuggingFace...', flush=True)

        # Download to temporary storage (emptyDir)
        model_path = snapshot_download(
            repo_id=model_id,
            local_dir=temp_model_path,
            resume_download=True
        )

        print(f'✓ Download complete! Model at: {{model_path}}', flush=True)
    else:
        # Download files from S3 to temp storage for MLflow logging
        print(f'Downloading existing model from S3 to temp storage...', flush=True)
        os.makedirs(temp_model_path, exist_ok=True)

        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix)

        downloaded_count = 0
        for page in pages:
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                s3_key = obj['Key']
                # Get relative path by removing prefix
                relative_path = s3_key[len(s3_prefix):].lstrip('/')
                if not relative_path:  # Skip the prefix itself
                    continue

                local_file = os.path.join(temp_model_path, relative_path)
                os.makedirs(os.path.dirname(local_file), exist_ok=True)

                s3_client.download_file(s3_bucket, s3_key, local_file)
                downloaded_count += 1
                if downloaded_count % 10 == 0:
                    print(f'  Downloaded {{downloaded_count}} files from S3...', flush=True)

        print(f'✓ Downloaded {{downloaded_count}} files from S3 to temp storage', flush=True)

    # Re-authenticate with MLflow before registration (token may have expired during long download)
    print(f'Re-authenticating with MLflow before registration...', flush=True)
    token_response = requests.post(
        token_url,
        data={{
            'grant_type': 'password',
            'client_id': client_id,
            'client_secret': client_secret,
            'username': username,
            'password': password
        }},
        verify=False
    )
    token_response.raise_for_status()
    os.environ['MLFLOW_TRACKING_TOKEN'] = token_response.json()['access_token']
    print('✓ MLflow re-authentication successful', flush=True)

    # Register model in MLflow using transformers flavor
    print(f'Registering model in MLflow...', flush=True)

    import mlflow.transformers

    with mlflow.start_run(run_name=f"mirror-{{model_name}}") as run:
        # Log model metadata
        mlflow.log_params({{
            "source": "huggingface",
            "model_id": model_id,
            "download_method": "huggingface_hub",
            "task": model_task
        }})

        # Log model using transformers flavor - pass directory path
        print(f'Uploading model to MLflow (S3) using transformers flavor...', flush=True)
        mlflow.transformers.log_model(
            transformers_model=temp_model_path,
            artifact_path="model",
            task=model_task,
            registered_model_name=model_name,
            pip_requirements=[
                "transformers",
                "torch",
                "accelerate",
                "sentencepiece",
                "protobuf"
            ]
        )
        print(f'✓ Model uploaded and registered in MLflow', flush=True)

    print(f'✓ Model registered in MLflow as: {{model_name}}', flush=True)
    print(f'✓ Model stored in S3: s3://{{s3_bucket}}/{{s3_prefix}}/', flush=True)

except Exception as e:
    print(f'Error during download/registration: {{e}}', flush=True)
    import traceback
    traceback.print_exc()
    raise
"""

            # Container environment variables
            env_vars = [
                hera_models.EnvVar(
                    name="PYTHONUNBUFFERED",
                    value="1"  # Force Python to flush output immediately
                ),
                hera_models.EnvVar(
                    name="HF_HUB_VERBOSITY",
                    value="info"  # Enable huggingface_hub progress bars
                ),
                hera_models.EnvVar(
                    name="TQDM_POSITION",
                    value="-1"  # Force tqdm to stay enabled even in non-TTY
                ),
                hera_models.EnvVar(
                    name="HF_TOKEN",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="huggingface-token",
                            key="token"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="MLFLOW_TRACKING_URI",
                    value=self.mlflow_uri
                ),
                # MLflow Authentication via Keycloak
                hera_models.EnvVar(
                    name="MLFLOW_KEYCLOAK_TOKEN_URL",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="keycloak-token-url"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="MLFLOW_KEYCLOAK_CLIENT_ID",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="client-id"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="MLFLOW_CLIENT_SECRET",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="client-secret"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="MLFLOW_AUTH_USERNAME",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="username"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="MLFLOW_AUTH_PASSWORD",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="password"
                        )
                    )
                ),
                # XET Configuration - Disable XET due to stability issues
                # XET HIGH_PERFORMANCE mode causes "CAS service error: Request failed after 5 retries"
                # Network diagnostics show high latency variance (22-71ms) which combined with
                # HIGH_PERFORMANCE's 16-128 concurrent connections causes connection timeouts
                # Disabling XET entirely to use standard HTTP downloads for stability
                hera_models.EnvVar(
                    name="HF_HUB_DISABLE_XET",
                    value="1"  # Disable XET to avoid CAS service stability issues
                ),
                # S3 Configuration for SeaweedFS
                hera_models.EnvVar(
                    name="AWS_ACCESS_KEY_ID",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="seaweedfs-s3-secret",
                            key="access_key"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="AWS_SECRET_ACCESS_KEY",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="seaweedfs-s3-secret",
                            key="secret_key"
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="AWS_S3_ENDPOINT",
                    value="http://seaweedfs-filer.seaweedfs.svc.cluster.local:8333"
                )
            ]

            # Get Harbor registry from environment
            domain_name = os.getenv("DOMAIN_NAME", "thinkube.com")
            harbor_registry = f"registry.{domain_name}"
            model_mirror_image = f"{harbor_registry}/library/model-mirror:latest"

            # Download container
            Container(
                name="download",
                image=model_mirror_image,
                image_pull_secrets=[hera_models.LocalObjectReference(name="app-pull-secret")],
                command=["python", "-c"],
                args=[download_script],
                volume_mounts=[
                    hera_models.VolumeMount(name="download-cache", mount_path="/tmp/downloads")
                ],
                env=env_vars,
                resources=hera_models.ResourceRequirements(
                    requests={"memory": "4Gi", "cpu": "1"},
                    limits={"memory": "12Gi", "cpu": "2"}
                )
            )

        # Submit workflow to Argo
        result = w.create()
        workflow_name = result.metadata.name

        logger.info(f"Workflow submitted: {workflow_name}")
        return workflow_name

    def get_download_status(self, workflow_name: str) -> Dict:
        """
        Get status of a download workflow

        Args:
            workflow_name: Workflow name/ID

        Returns:
            Dictionary with workflow status information
        """
        try:
            workflow = self.custom_api.get_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=self.workflow_namespace,
                plural="workflows",
                name=workflow_name
            )

            status = workflow.get("status", {})
            phase = status.get("phase", "Unknown")
            started_at = status.get("startedAt")
            finished_at = status.get("finishedAt")
            message = status.get("message", "")

            # Extract model_id from workflow labels
            metadata = workflow.get("metadata", {})
            labels = metadata.get("labels", {})
            model_label = labels.get("model-id", "")
            # Convert label back to model_id format (replace - with /)
            model_id = model_label.replace("-", "/", 1) if model_label else None

            return {
                "workflow_name": workflow_name,
                "model_id": model_id,
                "status": phase,
                "started_at": started_at,
                "finished_at": finished_at,
                "message": message,
                "is_running": phase in ["Pending", "Running"],
                "is_complete": phase == "Succeeded",
                "is_failed": phase in ["Failed", "Error"]
            }

        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Workflow not found: {workflow_name}")
                return {
                    "workflow_name": workflow_name,
                    "status": "NotFound",
                    "is_running": False,
                    "is_complete": False,
                    "is_failed": True
                }
            raise

    def list_active_downloads(self) -> List[Dict]:
        """
        List all active (running or pending) download workflows

        Returns:
            List of workflow status dictionaries
        """
        try:
            workflows = self.custom_api.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=self.workflow_namespace,
                plural="workflows",
                label_selector="workflows.argoproj.io/phase in (Pending,Running)"
            )

            active = []
            for wf in workflows.get("items", []):
                wf_name = wf.get("metadata", {}).get("name")
                if not wf_name:
                    continue
                # Filter for model download workflows (generated with "model-dl-" prefix)
                if wf_name.startswith("model-dl-"):
                    status = self.get_download_status(wf_name)
                    active.append(status)

            return active

        except ApiException as e:
            logger.error(f"Failed to list workflows: {e}")
            return []

    def check_all_models_exist(self) -> Dict[str, bool]:
        """
        Check which models are already registered in MLflow model registry.

        Returns:
            Dictionary mapping model_id to existence status
        """
        try:
            # Get MLflow tracking URI from environment or use default
            mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")
            mlflow_client = MlflowClient(tracking_uri=mlflow_uri)

            results = {}
            for model in AVAILABLE_MODELS:
                model_id = model["id"]
                # Convert model_id to MLflow model name format
                model_name = model_id.replace('/', '-')

                try:
                    # Check if model exists in MLflow registry
                    mlflow_client.get_registered_model(model_name)
                    # Model exists if we get here without exception
                    results[model_id] = True
                    logger.debug(f"Model {model_id} exists in MLflow registry")
                except RestException:
                    # Model doesn't exist in registry
                    results[model_id] = False
                    logger.debug(f"Model {model_id} not found in MLflow registry")

            return results

        except Exception as e:
            logger.error(f"Failed to check MLflow model registry: {e}")
            # Return empty dict on error (all models will show as not downloaded)
            return {}

    def check_model_exists(self, model_id: str) -> bool:
        """
        Check if a model is already registered in MLflow model registry

        Args:
            model_id: HuggingFace model ID

        Returns:
            True if model exists in MLflow registry, False otherwise
        """
        results = self.check_all_models_exist()
        return results.get(model_id, False)

    def cancel_download(self, workflow_name: str) -> bool:
        """
        Cancel a running download workflow

        Args:
            workflow_name: Workflow name/ID

        Returns:
            True if successfully cancelled
        """
        try:
            # Terminate workflow
            workflow = self.custom_api.get_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=self.workflow_namespace,
                plural="workflows",
                name=workflow_name
            )

            # Set workflow to terminate
            workflow["spec"]["shutdown"] = "Terminate"

            self.custom_api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=self.workflow_namespace,
                plural="workflows",
                name=workflow_name,
                body=workflow
            )

            logger.info(f"Workflow cancelled: {workflow_name}")
            return True

        except ApiException as e:
            logger.error(f"Failed to cancel workflow {workflow_name}: {e}")
            return False

    def delete_model(self, model_id: str) -> bool:
        """
        Delete a model from MLflow registry

        Args:
            model_id: Model identifier (e.g., "nvidia/Llama-3.1-8B-Instruct-FP8")

        Returns:
            bool: True if deleted successfully, False otherwise
        """
        try:
            # Convert model_id to MLflow model name format
            model_name = model_id.replace('/', '-')

            # Get MLflow tracking URI
            mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")
            mlflow_client = MlflowClient(tracking_uri=mlflow_uri)

            # Delete the registered model (this deletes all versions)
            mlflow_client.delete_registered_model(model_name)

            logger.info(f"Successfully deleted model {model_id} ({model_name}) from MLflow")
            return True

        except RestException as e:
            if "RESOURCE_DOES_NOT_EXIST" in str(e):
                logger.warning(f"Model {model_id} not found in MLflow registry")
                return True  # Already deleted, consider it success
            logger.error(f"Failed to delete model {model_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete model {model_id}: {e}")
            return False
