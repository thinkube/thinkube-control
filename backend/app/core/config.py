# app/core/config.py
from typing import List, Optional
from pydantic import validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "K8s Dashboard Hub"

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

    # Domain name for service discovery
    DOMAIN_NAME: str

    # PostgreSQL settings - all from environment
    POSTGRES_HOST: str = "postgresql-official.postgres.svc.cluster.local"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "thinkube_control"  # Main database for auth/tokens

    # CI/CD monitoring database
    CICD_DB_NAME: str = "cicd_monitoring"

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

    # Dashboard service URLs - configured via environment variables
    SEAWEEDFS_URL: str
    HARBOR_URL: str
    GITEA_URL: str
    ARGOCD_URL: str
    ARGO_WORKFLOWS_URL: str
    # Services not yet deployed - uncomment when ready
    # OPENSEARCH_URL: str
    # QDRANT_URL: str
    # AWX_URL: str
    # PGADMIN_URL: str
    # DEVPI_URL: str
    # JUPYTERHUB_URL: str
    # CODE_SERVER_URL: str
    # MKDOCS_URL: str

    class Config:
        case_sensitive = True
        env_file = ".env"


settings = Settings()
