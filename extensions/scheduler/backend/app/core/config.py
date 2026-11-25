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

    # PostgreSQL settings - all from environment
    POSTGRES_HOST: str = "postgresql-official.postgres.svc.cluster.local"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = os.environ.get("APP_NAME", "webapp").replace('-', '_')

    # The cluster's domain: Thinkube Control is control.<domain>
    DOMAIN_NAME: str

    @property
    def CONTROL_API_URL(self) -> str:
        return f"https://control.{self.DOMAIN_NAME}/api/v1"

    # The node-metrics pods of Thinkube Control, one on each node, which say what each GPU holds. The names are
    # not NODE_METRICS_*: Kubernetes gives every pod of the namespace variables of that name for the Service
    # node-metrics
    SCHEDULER_METRICS_NAMESPACE: str = "thinkube-control"
    SCHEDULER_METRICS_PORT: int = 9100

    # The cluster's registry, where a job's image is
    @property
    def REGISTRY(self) -> str:
        return f"registry.{self.DOMAIN_NAME}"

    # The namespace of the jobs' pods, which holds the claims of the folders a job mounts
    # (ansible/40_thinkube/core/thinkube-control/14_configure_scheduler_runs.yaml of thinkube)
    SCHEDULER_RUNS_NAMESPACE: str = "scheduler-runs"

    # The owner of the shared folders, as a notebook server and the IDE have it: a job runs as this user
    SCHEDULER_JOB_UID: int = 1000
    SCHEDULER_JOB_GID: int = 100

    # Whether this process runs the worker, which follows the jobs and starts them; the tests run without it
    SCHEDULER_WORKER: bool = True

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
