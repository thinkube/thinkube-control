# ⚠️ Under Development - Not Ready for Use

## MLflow Integration

All deployed applications automatically receive MLflow authentication credentials as environment variables, enabling them to query the MLflow Model Registry and access models stored in JuiceFS.

### Automatic Environment Variable Injection

The platform automatically injects the following MLflow-related environment variables into every deployment:

- `MLFLOW_KEYCLOAK_TOKEN_URL` - Keycloak token endpoint for OAuth2 authentication
- `MLFLOW_KEYCLOAK_CLIENT_ID` - Keycloak client ID for MLflow
- `MLFLOW_CLIENT_SECRET` - Keycloak client secret
- `MLFLOW_AUTH_USERNAME` - MLflow username
- `MLFLOW_AUTH_PASSWORD` - MLflow password
- `ADMIN_PASSWORD` - Admin password (legacy compatibility)
- `SEAWEEDFS_PASSWORD` - S3-compatible password for direct model access

### How It Works

1. **Secret Replication** ([playbooks/deploy-application.yaml:166-177](playbooks/deploy-application.yaml#L166-L177))
   - During deployment, the `mlflow-auth-config` secret is copied from `thinkube-control` namespace to each application namespace

2. **Credential Extraction** ([playbooks/deploy-application.yaml:179-185](playbooks/deploy-application.yaml#L179-L185))
   - All MLflow auth keys are extracted and made available as Ansible variables

3. **Secret Generation** ([templates/k8s/mlflow-secrets.j2](templates/k8s/mlflow-secrets.j2))
   - A `{app-name}-mlflow-credentials` secret is generated containing all auth credentials

4. **Environment Variable Injection** ([templates/k8s/deployment-separate.j2:126-160](templates/k8s/deployment-separate.j2#L126-L160))
   - All deployments automatically receive these credentials as environment variables via `secretRef`

### Usage in Templates

Templates can use these environment variables without declaring them in `thinkube.yaml` (which would violate the spec):

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

See [tkt-tensorrt-llm](https://github.com/thinkube/tkt-tensorrt-llm) for a complete example.

## License

Apache License 2.0 - See [LICENSE](LICENSE)

## Copyright

Copyright 2025 Alejandro Martínez Corriá
