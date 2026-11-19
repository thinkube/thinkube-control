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

    # The tasks repository: the folder "mill" of the shared file system, which
    # the code-server playbook of thinkube also mounts in the IDE
    MILL_REPOSITORY: str = "/storage/thinkube-shared/mill"

    # The owner of the tasks repository, as the shared folders have it. A
    # task runs as this user, so the IDE can change what a run wrote.
    MILL_TASK_UID: int = 1000
    MILL_TASK_GID: int = 100

    # The home folder of a task's process
    MILL_TASK_HOME: str = "/tmp/mill-home"

    # The platform's LLM Gateway, in the cluster: a task that calls a model reads it as LLM_GATEWAY_URL
    MILL_LLM_GATEWAY_URL: str = "http://llm-proxy.thinkube-control.svc.cluster.local:8080"

    # The platform's NATS, at the address its playbook gives it
    # (ansible/40_thinkube/optional/nats/10_deploy.yaml of thinkube), and the
    # prefix of the subjects the runs publish on
    MILL_NATS_URL: str = "nats://nats.nats.svc.cluster.local:4222"
    MILL_SUBJECTS: str = "mill"

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
