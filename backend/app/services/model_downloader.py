"""
Model Downloader Service

Manages HuggingFace model downloads to thinkube-models PVC using Argo Workflows via Hera.
"""

import os
import logging
from typing import List, Dict, Optional
from datetime import datetime

from hera.workflows import Workflow, Container, models as hera_models
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
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "openai/gpt-oss-120b",
        "name": "GPT-OSS 120B",
        "size": "~70GB",
        "quantization": "FP4",
        "description": "OpenAI GPT-OSS 120B model optimized for TensorRT-LLM",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Llama-3.1-8B-Instruct-FP8",
        "name": "Llama 3.1 8B Instruct",
        "size": "~8GB",
        "quantization": "FP8",
        "description": "Meta Llama 3.1 8B instruction-tuned model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Llama-3.1-8B-Instruct-FP4",
        "name": "Llama 3.1 8B Instruct",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Meta Llama 3.1 8B instruction-tuned model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Llama-3.3-70B-Instruct-FP4",
        "name": "Llama 3.3 70B Instruct",
        "size": "~35GB",
        "quantization": "FP4",
        "description": "Meta Llama 3.3 70B instruction-tuned model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-8B-FP8",
        "name": "Qwen3 8B",
        "size": "~8GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen3 8B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-8B-FP4",
        "name": "Qwen3 8B",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 8B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-14B-FP8",
        "name": "Qwen3 14B",
        "size": "~14GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen3 14B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-14B-FP4",
        "name": "Qwen3 14B",
        "size": "~7GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 14B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-32B-FP4",
        "name": "Qwen3 32B",
        "size": "~16GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 32B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Phi-4-multimodal-instruct-FP8",
        "name": "Phi-4 Multimodal Instruct",
        "size": "~6GB",
        "quantization": "FP8",
        "description": "Microsoft Phi-4 multimodal instruction-tuned model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Phi-4-multimodal-instruct-FP4",
        "name": "Phi-4 Multimodal Instruct",
        "size": "~3GB",
        "quantization": "FP4",
        "description": "Microsoft Phi-4 multimodal instruction-tuned model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Phi-4-reasoning-plus-FP8",
        "name": "Phi-4 Reasoning Plus",
        "size": "~6GB",
        "quantization": "FP8",
        "description": "Microsoft Phi-4 reasoning-focused model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Phi-4-reasoning-plus-FP4",
        "name": "Phi-4 Reasoning Plus",
        "size": "~3GB",
        "quantization": "FP4",
        "description": "Microsoft Phi-4 reasoning-focused model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Llama-3_3-Nemotron-Super-49B-v1_5-FP8",
        "name": "Llama 3.3 Nemotron Super 49B",
        "size": "~49GB",
        "quantization": "FP8",
        "description": "NVIDIA Nemotron variant of Llama 3.3",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen3-30B-A3B-FP4",
        "name": "Qwen3 30B-A3B",
        "size": "~15GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen3 30B-A3B model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen2.5-VL-7B-Instruct-FP8",
        "name": "Qwen2.5 VL 7B Instruct",
        "size": "~7GB",
        "quantization": "FP8",
        "description": "Alibaba Qwen2.5 Vision-Language model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Qwen2.5-VL-7B-Instruct-FP4",
        "name": "Qwen2.5 VL 7B Instruct",
        "size": "~4GB",
        "quantization": "FP4",
        "description": "Alibaba Qwen2.5 Vision-Language model",
        "server_type": ["tensorrt-llm"]
    },
    {
        "id": "nvidia/Llama-4-Scout-17B-16E-Instruct-FP4",
        "name": "Llama 4 Scout 17B-16E Instruct",
        "size": "~9GB",
        "quantization": "FP4",
        "description": "Meta Llama 4 Scout model",
        "server_type": ["tensorrt-llm"]
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
        self.argo_namespace = "argo"
        self.models_pvc_name = "thinkube-models"
        self.models_namespace = "thinkube-control"
        self.parallelism = 3  # Max concurrent downloads

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
        # Validate model exists in catalog
        if not any(m["id"] == model_id for m in AVAILABLE_MODELS):
            raise ValueError(f"Model '{model_id}' not found in catalog")

        logger.info(f"Creating download workflow for model: {model_id}")

        # Create Hera workflow
        with Workflow(
            generate_name="model-dl-",
            namespace=self.argo_namespace,
            parallelism=self.parallelism,
            volumes=[
                hera_models.Volume(
                    name="models-pvc",
                    persistent_volume_claim={"claim_name": self.models_pvc_name}
                )
            ]
        ) as w:
            # Download script with MLflow registration
            download_script = f"""
import os
import shutil
from huggingface_hub import snapshot_download
import mlflow

# Set HuggingFace cache to temp location on PVC
temp_cache = '/models/temp-downloads'
os.makedirs(temp_cache, exist_ok=True)
os.environ['HF_HOME'] = temp_cache

# Configure MLflow tracking URI
mlflow.set_tracking_uri('http://mlflow.mlflow.svc.cluster.local:5000')

print(f'Starting download of {model_id}...')
print(f'Temporary cache directory: {{temp_cache}}')

# Download model files to temp location
path = snapshot_download(
    repo_id='{model_id}',
    cache_dir=temp_cache,
    resume_download=True
)

print(f'✓ Download complete!')
print(f'Model path: {{path}}')

# Register model in MLflow by logging the files
print(f'Registering model in MLflow...')
model_name = '{model_id}'.replace('/', '-')

try:
    with mlflow.start_run(run_name=f"mirror-{{model_name}}"):
        # Log the model directory as an artifact (copies to MLflow artifact store)
        mlflow.log_artifact(path, artifact_path="model")

        # Register the model
        mlflow.register_model(
            f"runs:/{{mlflow.active_run().info.run_id}}/model",
            model_name
        )

    print(f'✓ Model registered in MLflow as: {{model_name}}')

    # Delete temporary files after successful MLflow registration
    print(f'Cleaning up temporary download cache...')
    shutil.rmtree(temp_cache)
    print(f'✓ Temporary files deleted')
except Exception as e:
    print(f'Error: Could not register model in MLflow: {{e}}')
    raise
"""

            # Container environment variables - HF_TOKEN from secret
            env_vars = [
                hera_models.EnvVar(
                    name="HF_TOKEN",
                    value_from=hera_models.EnvVarSource(
                        secret_key_ref=hera_models.SecretKeySelector(
                            name="huggingface-token",
                            key="token"
                        )
                    )
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
                command=["python", "-c"],
                args=[download_script],
                volume_mounts=[
                    hera_models.VolumeMount(name="models-pvc", mount_path="/models")
                ],
                env=env_vars,
                resources=hera_models.ResourceRequirements(
                    requests={"memory": "2Gi", "cpu": "1"},
                    limits={"memory": "4Gi", "cpu": "2"}
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
                namespace=self.argo_namespace,
                plural="workflows",
                name=workflow_name
            )

            status = workflow.get("status", {})
            phase = status.get("phase", "Unknown")
            started_at = status.get("startedAt")
            finished_at = status.get("finishedAt")
            message = status.get("message", "")

            # Extract model_id from workflow parameters if available
            model_id = None
            spec = workflow.get("spec", {})
            templates = spec.get("templates", [])
            if templates:
                # Try to extract from container args
                for template in templates:
                    if "container" in template:
                        args = template["container"].get("args", [])
                        for arg in args:
                            if "repo_id=" in arg:
                                # Extract model_id from download script
                                try:
                                    model_id = arg.split("repo_id='")[1].split("'")[0]
                                except:
                                    pass

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
                namespace=self.argo_namespace,
                plural="workflows",
                label_selector="workflows.argoproj.io/phase in (Pending,Running)"
            )

            active = []
            for wf in workflows.get("items", []):
                wf_name = wf["metadata"]["name"]
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
                namespace=self.argo_namespace,
                plural="workflows",
                name=workflow_name
            )

            # Set workflow to terminate
            workflow["spec"]["shutdown"] = "Terminate"

            self.custom_api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=self.argo_namespace,
                plural="workflows",
                name=workflow_name,
                body=workflow
            )

            logger.info(f"Workflow cancelled: {workflow_name}")
            return True

        except ApiException as e:
            logger.error(f"Failed to cancel workflow {workflow_name}: {e}")
            return False
