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
