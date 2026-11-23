# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/core/config.py
import os
from typing import List, Optional
from pydantic import validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = os.environ.get("APP_TITLE", "Web Application")

    # CORS settings
    BACKEND_CORS_ORIGINS: List[str] = []

    # Keycloak settings
    KEYCLOAK_URL: str
    KEYCLOAK_REALM: str
    KEYCLOAK_CLIENT_ID: str
    KEYCLOAK_CLIENT_SECRET: str
    KEYCLOAK_VERIFY_SSL: bool = True

    # Frontend URL for redirects
    FRONTEND_URL: str

    # The cluster's domain: Thinkube Control is control.<domain> and MLflow
    # experiments.<domain>, as for the notebook servers.
    DOMAIN_NAME: str

    # Mill's task runs: the namespace their pods run in, with the claim of
    # the shared file system's root (the playbook
    # thinkube-control/14_configure_mill_runs.yaml of thinkube), the image
    # their pods start from (the AI base image, a manifest of both
    # architectures), and the NATS a run reports to, as Mill's main pod has them
    MILL_RUNS_NAMESPACE: str = "mill-runs"
    MILL_SHARED_CLAIM: str = "thinkube-shared"
    MILL_SHARED_MOUNT: str = "/storage/thinkube-shared"
    MILL_REPOSITORY: str = "/storage/thinkube-shared/mill"
    MILL_TASK_IMAGE: str = "library/ai-inference-base:cuda13.0-torch2.9-py3.12"
    MILL_NATS_URL: str = "nats://nats.nats.svc.cluster.local:4222"
    # The platform's LLM Gateway, in the cluster: a task that calls a model reads it as LLM_GATEWAY_URL
    MILL_LLM_GATEWAY_URL: str = "http://llm-proxy.thinkube-control.svc.cluster.local:8080"
    MILL_SUBJECTS: str = "mill"
    # The owner of the tasks repository, as the shared folders have it
    MILL_TASK_UID: int = 1000
    MILL_TASK_GID: int = 100
    # The folder of each task run's state, below the shared file system: kept across the run's starts
    MILL_STATE_FOLDER: str = "forge-runs"
    # A task that names a venv of the notebook servers runs on their image, which the venvs are built for, with
    # the node's venvs mounted where a notebook server has them
    MILL_VENV_IMAGE: str = "library/tk-jupyter-base:latest"
    MILL_VENVS_HOST_PATH: str = "/var/lib/jupyterhub-venvs"
    MILL_VENVS_MOUNT: str = "/home/thinkube/venvs"
    # MLflow's file system (the playbook thinkube-control/14_configure_mill_runs.yaml), where the mirrored models
    # are, mounted read-only by a task that names models, where a notebook server has it
    MILL_MODELS_CLAIM: str = "mlflow-root"
    MILL_MODELS_MOUNT: str = "/home/thinkube/thinkube/mlflow"

    @property
    def REGISTRY(self) -> str:
        return f"registry.{self.DOMAIN_NAME}"

    @property
    def PACKAGE_INDEX(self) -> str:
        return f"https://packages-api.{self.DOMAIN_NAME}/root/stable/+simple/"

    # The MLflow service account every app gets, to read the runs' metrics
    MLFLOW_KEYCLOAK_TOKEN_URL: str
    MLFLOW_KEYCLOAK_CLIENT_ID: str
    MLFLOW_CLIENT_SECRET: str
    MLFLOW_AUTH_USERNAME: str
    MLFLOW_AUTH_PASSWORD: str

    # The training queue's worker runs in the backend (app/services/worker.py);
    # the tests run the API without it
    QUEUE_WORKER: bool = True

    @property
    def CONTROL_API_URL(self) -> str:
        return f"https://control.{self.DOMAIN_NAME}/api/v1"

    @property
    def MLFLOW_URL(self) -> str:
        return f"https://experiments.{self.DOMAIN_NAME}"

    # PostgreSQL settings - all from environment
    POSTGRES_HOST: str = "postgresql-official.postgres.svc.cluster.local"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = os.environ.get("APP_NAME", "webapp").replace('-', '_')

    # Allow DATABASE_URL to be overridden by environment
    DATABASE_URL: Optional[str] = None

    @validator("DATABASE_URL", pre=True)
    def construct_database_url(cls, v, values):
        """Use DATABASE_URL from environment or construct from parts."""
        if v:
            return v
        # Construct from individual settings
        user = values.get("POSTGRES_USER")
        password = values.get("POSTGRES_PASSWORD")
        host = values.get("POSTGRES_HOST")
        port = values.get("POSTGRES_PORT")
        db = values.get("POSTGRES_DB")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    class Config:
        case_sensitive = True
        env_file = ".env"


settings = Settings()
