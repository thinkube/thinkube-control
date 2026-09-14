"""The identity that writes into application namespaces.

The first deploy (scripts/deploy_application.py) loads /home/thinkube/.kube/config.
The backend's own service account only reads Secrets, so everything the backend
writes into an application's namespace after the first deploy uses that same
kubeconfig: the application's Secret, and the restarts that carry a changed
value to its pods.
"""

from pathlib import Path

from kubernetes import client, config

DEPLOY_KUBECONFIG = Path("/home/thinkube/.kube/config")

# A patch body here is a partial object. The client would otherwise send a dict
# as a JSON Patch, which the API refuses.
MERGE_PATCH = "application/merge-patch+json"


def deploy_api_client() -> client.ApiClient:
    if not DEPLOY_KUBECONFIG.exists():
        raise RuntimeError(
            f"{DEPLOY_KUBECONFIG} not found. Writes into application namespaces use "
            "the kubeconfig the first deploy uses."
        )
    return config.new_client_from_config(config_file=str(DEPLOY_KUBECONFIG))
