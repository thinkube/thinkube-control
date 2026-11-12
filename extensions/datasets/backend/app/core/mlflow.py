# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""MLflow access: the service account the platform gives every app.

Kept apart from the app's settings so that a workflow step, which reaches
MLflow and nothing else, needs only these variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MlflowSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, env_file=".env", extra="ignore")

    # MLflow is experiments.<domain>
    DOMAIN_NAME: str
    MLFLOW_KEYCLOAK_TOKEN_URL: str
    MLFLOW_KEYCLOAK_CLIENT_ID: str
    MLFLOW_CLIENT_SECRET: str
    MLFLOW_AUTH_USERNAME: str
    MLFLOW_AUTH_PASSWORD: str

    @property
    def MLFLOW_URL(self) -> str:
        return f"https://experiments.{self.DOMAIN_NAME}"


mlflow_settings = MlflowSettings()
