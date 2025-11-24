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
                    name="juicefs-mlflow",
                    persistent_volume_claim=hera_models.PersistentVolumeClaimVolumeSource(
                        claim_name="juicefs-mlflow"
                    )
                )
            ]
        ) as w:
            # Download script - Manual S3 upload to JuiceFS Gateway (MLflow 3.0 workaround)
            download_script = f"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from huggingface_hub import snapshot_download
import mlflow
import mlflow.transformers
import boto3

# Force progress bars to show even without TTY
os.environ['TQDM_DISABLE'] = '0'
os.environ['TQDM_MININTERVAL'] = '10'

# MLflow authentication function (can be called multiple times to refresh token)
import requests

def refresh_mlflow_token():
    \"\"\"Refresh MLflow authentication token from Keycloak\"\"\"
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
    return True

# Get initial MLflow authentication token
print('Authenticating with MLflow...', flush=True)
refresh_mlflow_token()
print('✓ MLflow authentication successful', flush=True)

# Get MLflow URI from environment
mlflow_uri = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow.mlflow.svc.cluster.local:5000')
mlflow.set_tracking_uri(mlflow_uri)

model_id = '{safe_model_id}'
model_task = '{safe_model_task}'
model_name = model_id.replace('/', '-')

print(f'Model: {{model_id}}', flush=True)
print(f'Registry name: {{model_name}}', flush=True)

# Configure S3 client for JuiceFS Gateway
s3_client = boto3.client(
    's3',
    endpoint_url=os.environ['AWS_S3_ENDPOINT'],
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
    region_name=os.environ['AWS_DEFAULT_REGION']
)
s3_bucket = 'mlflow'
print(f'✓ S3 client configured for JuiceFS Gateway', flush=True)

try:
    # Set up experiment
    experiment_name = "model-registry"
    client = mlflow.MlflowClient()
    try:
        experiment = mlflow.get_experiment_by_name(experiment_name)
        if experiment is None:
            # Experiment doesn't exist, create it
            experiment_id = mlflow.create_experiment(experiment_name)
            print(f'✓ Created experiment "{{experiment_name}}"', flush=True)
        elif experiment.lifecycle_stage == 'deleted':
            # Experiment is deleted, restore it
            print(f'Note: Experiment "{{experiment_name}}" is deleted, restoring it', flush=True)
            client.restore_experiment(experiment.experiment_id)
            experiment_id = experiment.experiment_id
            print(f'✓ Restored experiment "{{experiment_name}}"', flush=True)
        else:
            experiment_id = experiment.experiment_id
            print(f'✓ Using existing experiment "{{experiment_name}}"', flush=True)
    except Exception as exp_error:
        print(f'Warning: Could not create/get experiment: {{exp_error}}', flush=True)
        experiment_id = None

    if experiment_id:
        mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name=f"mirror-{{model_name}}") as run:
        run_id = run.info.run_id
        artifact_uri = run.info.artifact_uri

        print(f'Run ID: {{run_id}}', flush=True)
        print(f'Artifact URI: {{artifact_uri}}', flush=True)

        # CRITICAL: Extract S3 path from artifact_uri
        # artifact_uri format: s3://mlflow/artifacts/{{run_id}}/artifacts
        # We need: artifacts/{{run_id}}/artifacts/model for S3 upload
        s3_base_path = artifact_uri.replace('s3://mlflow/', '')
        s3_artifact_prefix = f'{{s3_base_path}}/model'
        print(f'S3 upload prefix: {{s3_artifact_prefix}}', flush=True)

        # Download to persistent staging area on JuiceFS (survives pod restarts)
        # This is OUTSIDE MLflow's artifact tree so it won't interfere with registration
        staging_base = '/mnt/juicefs/.staging'
        os.makedirs(staging_base, exist_ok=True)
        staging_model_path = f'{{staging_base}}/{{model_name}}'

        print(f'Downloading model from HuggingFace to staging: {{staging_model_path}}', flush=True)
        print(f'Note: Files persist across pod restarts, resume_download=True will skip existing files', flush=True)

        snapshot_download(
            repo_id=model_id,
            local_dir=staging_model_path,
            resume_download=True
        )
        print(f'✓ Model downloaded to staging area', flush=True)

        # Refresh token after long download (tokens expire after ~5-60 minutes)
        print('Refreshing MLflow authentication token...', flush=True)
        refresh_mlflow_token()
        print('✓ Token refreshed', flush=True)

        # Log model metadata
        mlflow.log_params({{
            "source": "huggingface",
            "model_id": model_id,
            "download_method": "persistent_staging_s3_upload",
            "task": model_task,
            "staging_path": staging_model_path
        }})

        # Manual S3 upload from staging - all model files
        print(f'Uploading model files from staging to S3 (JuiceFS Gateway)...', flush=True)
        upload_count = 0
        for root, dirs, files in os.walk(staging_model_path):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, staging_model_path)
                s3_key = f'{{s3_artifact_prefix}}/{{relative_path}}'

                with open(local_path, 'rb') as f:
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=s3_key,
                        Body=f
                    )
                upload_count += 1

        print(f'✓ Uploaded {{upload_count}} model files to S3', flush=True)

        # Create and upload MLflow metadata
        print(f'Creating MLflow metadata...', flush=True)
        temp_mlmodel_dir = tempfile.mkdtemp()
        mlflow.transformers.save_model(
            transformers_model=staging_model_path,
            path=temp_mlmodel_dir,
            task=model_task
        )

        # Upload metadata files via S3
        metadata_count = 0
        for metadata_file in ['MLmodel', 'requirements.txt', 'conda.yaml', 'python_env.yaml']:
            metadata_path = os.path.join(temp_mlmodel_dir, metadata_file)
            if os.path.exists(metadata_path):
                s3_key = f'{{s3_artifact_prefix}}/{{metadata_file}}'
                with open(metadata_path, 'rb') as f:
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=s3_key,
                        Body=f
                    )
                metadata_count += 1

        shutil.rmtree(temp_mlmodel_dir)
        print(f'✓ Uploaded {{metadata_count}} metadata files', flush=True)

        # Verify artifacts are accessible via MLflow client
        print(f'Verifying artifacts in MLflow...', flush=True)
        artifacts = client.list_artifacts(run_id, path='model')
        if artifacts:
            print(f'✓ Found {{len(artifacts)}} artifacts via MLflow client', flush=True)
        else:
            print(f'⚠ Warning: No artifacts found via MLflow client', flush=True)

        # Register the model using create_model_version (MLflow 3.0 compatible)
        print(f'Registering model in MLflow...', flush=True)
        model_uri = f'runs:/{{run_id}}/model'

        # Ensure registered model exists
        try:
            client.create_registered_model(model_name)
            print(f'✓ Created registered model: {{model_name}}', flush=True)
        except Exception as e:
            if 'already exists' not in str(e).lower():
                print(f'Warning creating registered model: {{e}}', flush=True)
            else:
                print(f'✓ Registered model already exists: {{model_name}}', flush=True)

        # Create model version (this is what makes it visible in UI)
        version = client.create_model_version(
            name=model_name,
            source=model_uri,
            run_id=run_id
        )
        print(f'✓ Model registered: {{model_name}} v{{version.version}} ({{version.status}})', flush=True)

        # Clean up staging files on success
        print(f'Cleaning up staging area: {{staging_model_path}}', flush=True)
        shutil.rmtree(staging_model_path)
        print(f'✓ Staging area cleaned up', flush=True)

    print(f'✓ Model mirroring completed: {{model_name}}', flush=True)
    print(f'  - Downloaded to persistent staging (resumable)', flush=True)
    print(f'  - Uploaded via S3 to JuiceFS Gateway', flush=True)
    print(f'  - Files accessible via POSIX at /mnt/juicefs/{{s3_base_path}}/model', flush=True)
    print(f'  - Registered in MLflow Model Registry', flush=True)
    print(f'  - Staging files cleaned up', flush=True)

except Exception as e:
    print(f'Error during download/registration: {{e}}', flush=True)
    import traceback
    traceback.print_exc()
    import sys
    sys.exit(1)
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
                # S3 Configuration for JuiceFS Gateway (manual upload workaround)
                hera_models.EnvVar(
                    name="AWS_ACCESS_KEY_ID",
                    value="tkadmin"
                ),
                hera_models.EnvVar(
                    name="AWS_SECRET_ACCESS_KEY",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="mlflow-auth-config",
                            key="password"  # Uses admin password for JuiceFS Gateway
                        )
                    )
                ),
                hera_models.EnvVar(
                    name="AWS_S3_ENDPOINT",
                    value="http://juicefs-mlflow-gateway.juicefs.svc.cluster.local:9001"
                ),
                hera_models.EnvVar(
                    name="AWS_DEFAULT_REGION",
                    value="us-east-1"
                ),
                # MLflow S3 configuration for artifact listing/reading
                hera_models.EnvVar(
                    name="MLFLOW_S3_ENDPOINT_URL",
                    value="http://juicefs-mlflow-gateway.juicefs.svc.cluster.local:9001"
                ),
                hera_models.EnvVar(
                    name="MLFLOW_S3_IGNORE_TLS",
                    value="true"
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
                    hera_models.VolumeMount(name="juicefs-mlflow", mount_path="/mnt/juicefs")
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
        Check which models have been successfully downloaded by querying the database.

        Returns:
            Dictionary mapping model_id to existence status
        """
        try:
            from app.db.session import SessionLocal
            from app.models.model_mirrors import ModelMirrorJob

            db = SessionLocal()
            try:
                # Query all successfully completed mirror jobs
                succeeded_jobs = db.query(ModelMirrorJob).filter(
                    ModelMirrorJob.status == "succeeded"
                ).all()

                # Build result dict - all models are False by default
                results = {model["id"]: False for model in AVAILABLE_MODELS}

                # Mark models as True if they have a succeeded job
                for job in succeeded_jobs:
                    if job.model_id in results:
                        results[job.model_id] = True
                        logger.debug(f"Model {job.model_id} found in database as succeeded")

                return results

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to check model download status from database: {e}")
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
