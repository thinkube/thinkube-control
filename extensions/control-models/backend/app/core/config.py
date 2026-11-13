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

    # The branch of the platform this cluster follows: the platform's catalogue is
    # read at it. Thinkube Control passes it when it deploys the extension.
    THINKUBE_BRANCH: str

    # The GitHub account whose metadata repository holds the user's catalogue
    # entries, and its token; no account means no user catalogue
    GITHUB_USERNAME: str = ""
    GITHUB_TOKEN: str = ""

    # MLflow's tracking server and Ollama, platform services at fixed addresses
    MLFLOW_TRACKING_URI: str = "http://mlflow.mlflow.svc.cluster.local:5000"
    OLLAMA_URL: str = "http://ollama.ollama.svc.cluster.local:11434"

    # Follow the mirror workflows; off where there is no cluster to ask, as in the tests
    BACKGROUND_WORK: bool = True

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
