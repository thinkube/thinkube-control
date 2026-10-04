# thinkube-control

The management service of the Thinkube platform: an API, a web interface and an MCP server.

## What it does

- **API.** A FastAPI backend under `/api/v1` (`backend/app/api/`). It covers services and their health, template deployment, optional components, Harbor images and mirroring, custom images, models and model mirrors, the LLM gateway, JupyterHub settings, virtual environments and notebooks, secrets, API tokens, Knative services, nodes and CI/CD runs.
- **Web interface.** A React application (`frontend/`) built with `thinkube-style`. Its pages include the dashboard, Templates, Optional Components, Harbor Images, Models, LLM Gateway, Secrets, API Tokens, JupyterHub settings, Jupyter kernels, Knative services and Nodes.
- **MCP server.** The API is also served as MCP tools, resources and prompts at `/mcp` (`backend/app/__init__.py`, `backend/fastapi-mcp-extended/`).
- **Template deployment.** `scripts/deploy_application.py` deploys an application from a template repository. It writes the Kubernetes manifests from the Jinja templates in `templates/k8s/` and the Secrets those manifests name.
- **Sign-in.** Keycloak OAuth2/OpenID Connect. See [AUTHENTICATION.md](AUTHENTICATION.md).

## How it reaches a user

thinkube-control is a core component. The Thinkube installer deploys it with the playbooks in `ansible/40_thinkube/core/thinkube-control/` of [thinkube](https://github.com/thinkube/thinkube). It is not installed on its own.

## MLflow Integration

Every application deployed from a template receives MLflow authentication credentials as environment variables. With them it can query the MLflow Model Registry and read models from storage.

### Environment variables set in every container

From the `{app-name}-mlflow-credentials` Secret:

- `MLFLOW_KEYCLOAK_TOKEN_URL` - Keycloak token endpoint for OAuth2 authentication
- `MLFLOW_KEYCLOAK_CLIENT_ID` - Keycloak client ID for MLflow
- `MLFLOW_CLIENT_SECRET` - Keycloak client secret
- `MLFLOW_AUTH_USERNAME` - MLflow username
- `MLFLOW_AUTH_PASSWORD` - MLflow password
- `ADMIN_PASSWORD` - the same value as `MLFLOW_AUTH_PASSWORD`

From the `{app-name}-storage-credentials` Secret, for direct S3 access to SeaweedFS:

- `SEAWEEDFS_ENDPOINT` - the internal S3 endpoint (set as a plain value)
- `SEAWEEDFS_ACCESS_KEY` - S3 access key
- `SEAWEEDFS_SECRET_KEY` - S3 secret key

### How It Works

1. **Reading the credentials** ([scripts/deploy_application.py:417-426](scripts/deploy_application.py#L417-L426))
   - During deployment, the `mlflow-auth-config` Secret is read from the `thinkube-control` namespace. The SeaweedFS keys are read from `seaweedfs-s3-credentials` in the `seaweedfs` namespace (lines 428-432).

2. **Writing the application's Secrets** ([scripts/deploy_application.py:539-573](scripts/deploy_application.py#L539-L573), [scripts/platform_credentials.py:82-120](scripts/platform_credentials.py#L82-L120))
   - The `{app-name}-mlflow-credentials` and `{app-name}-storage-credentials` Secrets are written through the Kubernetes API in the application namespace. The manifests under the application's `k8s/` folder name these Secrets and hold no value.

3. **Copying the MLflow configuration** ([scripts/deploy_application.py:777-792](scripts/deploy_application.py#L777-L792))
   - The `mlflow-auth-config` Secret is also copied into the application namespace.

4. **Environment Variable Injection** ([templates/k8s/deployment-separate.j2:187-229](templates/k8s/deployment-separate.j2#L187-L229), [templates/k8s/knative-service.j2:144-186](templates/k8s/knative-service.j2#L144-L186))
   - Every container receives the variables through `secretKeyRef`, in both regular deployments and Knative services.

### Usage in Templates

A template uses these variables without declaring them in `thinkube.yaml`:

```python
import os
import requests

# Authenticate with MLflow
token_response = requests.post(
    os.environ['MLFLOW_KEYCLOAK_TOKEN_URL'],
    data={
        'grant_type': 'password',
        'client_id': os.environ['MLFLOW_KEYCLOAK_CLIENT_ID'],
        'client_secret': os.environ['MLFLOW_CLIENT_SECRET'],
        'username': os.environ['MLFLOW_AUTH_USERNAME'],
        'password': os.environ['MLFLOW_AUTH_PASSWORD'],
        'scope': 'openid'
    }
)
access_token = token_response.json()['access_token']

# Query MLflow Model Registry
mlflow_url = "http://mlflow.mlflow.svc.cluster.local"
response = requests.get(
    f"{mlflow_url}/api/2.0/mlflow/model-versions/search",
    params={'filter': f"name='{model_name}'"},
    headers={'Authorization': f'Bearer {access_token}'}
)
```

See [tkt-tensorrt-llm-harmony](https://github.com/thinkube/tkt-tensorrt-llm-harmony) (`server.py`) for a complete example.

## Working on it

The repository is a Copier template (`copier.yaml`). Files ending in `.jinja` are filled in with the cluster's values when it is deployed.

Backend (Python, FastAPI):

```bash
cd backend && uvicorn app:app --host 0.0.0.0 --port 8000 --reload
cd backend && ./run_tests.sh     # needs the PostgreSQL in the cluster
cd backend && pytest tests/test_api_endpoints.py -v
```

Frontend (React, Vite, TypeScript):

```bash
cd frontend && npm ci
cd frontend && npm run dev
cd frontend && npm run build
cd frontend && npm run lint
```

To try a change in a cluster, push it to `main` and run `ansible/40_thinkube/core/thinkube-control/12_deploy_dev.yaml` from the thinkube repository. Copier syncs the repository from GitHub, and the build and deploy run from there.

## License

Apache License 2.0 - See [LICENSE](LICENSE)

## Copyright

Copyright Alejandro Martínez Corriá and the Thinkube contributors
