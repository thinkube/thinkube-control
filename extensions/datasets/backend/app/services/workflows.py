# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Long work as Argo workflows in Thinkube Control's namespace, where
Datasets runs as an extension.

The workflows service of thinkube.yaml gives the backend's pods the account
that may submit them, and these variables:

- WORKFLOWS_NAMESPACE, WORKFLOWS_SERVER_URL, WORKFLOWS_SERVICE_ACCOUNT,
  WORKFLOWS_UI_URL
- WORKFLOWS_ARTIFACT_REPOSITORY: Datasets' own artifact repository, which a
  workflow in a namespace that several extensions share must name; it keeps
  the step logs under datasets/ in the bucket argo-artifacts
- CONTAINER_IMAGE_BACKEND: the image the backend runs, so a step runs the
  same code

A workflow outlives the backend: the backend records its name and follows
it again after a restart. Deleting the workflow cancels it.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from hera.exceptions import NotFound
from hera.workflows import Container, Env, Parameter, Resources, SecretEnv, Steps, Workflow, WorkflowsService
from hera.workflows.models import (Affinity, ArtifactRepositoryRef, NodeAffinity, NodeSelector, NodeSelectorRequirement,
                                   NodeSelectorTerm, TTLStrategy, ValueFrom)

from app.core.mlflow import mlflow_settings

SERVICE_ACCOUNT_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
VARIABLES = ("WORKFLOWS_NAMESPACE", "WORKFLOWS_SERVER_URL", "WORKFLOWS_SERVICE_ACCOUNT", "WORKFLOWS_UI_URL",
             "WORKFLOWS_ARTIFACT_REPOSITORY", "CONTAINER_IMAGE_BACKEND", "APP_NAME")
RESULT = "/tmp/result.json"
# An import streams Parquet through DuckDB: small, as Control's namespace
# quota is shared with Control and its other extensions
IMPORT_RESOURCES = Resources(cpu_request="250m", cpu_limit="1", memory_request="512Mi", memory_limit="2Gi")
# A finished workflow is kept a week: the backend reads its result when it
# next follows it, and its logs stay readable in Argo
KEEP_SECONDS = 7 * 24 * 3600
# On the nodes without a GPU, which keeps the step off the GPU nodes' memory
NO_GPU = Affinity(node_affinity=NodeAffinity(
    required_during_scheduling_ignored_during_execution=NodeSelector(node_selector_terms=[NodeSelectorTerm(
        match_expressions=[NodeSelectorRequirement(key="nvidia.com/gpu.count", operator="DoesNotExist")])])))
# The MLflow service account, from the Secret the platform writes for the app
MLFLOW_SECRET_KEYS = {
    "MLFLOW_KEYCLOAK_TOKEN_URL": "keycloak-token-url",
    "MLFLOW_KEYCLOAK_CLIENT_ID": "keycloak-client-id",
    "MLFLOW_CLIENT_SECRET": "mlflow-client-secret",
    "MLFLOW_AUTH_USERNAME": "mlflow-username",
    "MLFLOW_AUTH_PASSWORD": "admin-password",
}


class WorkflowsError(RuntimeError):
    """Argo refused or failed a call, or a variable it needs is missing; the message says which."""


@dataclass
class WorkflowState:
    # Pending, Running, Succeeded, Failed or Error
    phase: str
    message: Optional[str]
    # What the step wrote to its result, once the workflow has succeeded
    result: Optional[Dict[str, Any]]


def _env() -> Dict[str, str]:
    missing = [v for v in VARIABLES if not os.environ.get(v)]
    if missing:
        raise WorkflowsError(f"the variables {missing} are not set; declaring the workflows service in "
                             "thinkube.yaml gives them to the backend")
    return {v: os.environ[v] for v in VARIABLES}


def _service(env: Dict[str, str]) -> WorkflowsService:
    return WorkflowsService(host=env["WORKFLOWS_SERVER_URL"], verify_ssl=False,
                            token=SERVICE_ACCOUNT_TOKEN.read_text(), namespace=env["WORKFLOWS_NAMESPACE"])


def logs_url(name: str) -> str:
    return f"{_env()['WORKFLOWS_UI_URL']}/{name}"


def import_workflow(env: Dict[str, str], dataset_id: str, version: int, experiment_id: str, run_id: str,
                    first_row: int, by_split: Dict[str, List[Dict[str, Any]]]) -> Workflow:
    """The workflow of a Hugging Face import: one step, app.jobs.hub_import."""
    mlflow_secret = f"{env['APP_NAME']}-mlflow-credentials"
    with Workflow(
        generate_name=f"{env['APP_NAME']}-import-",
        entrypoint="import",
        namespace=env["WORKFLOWS_NAMESPACE"],
        service_account_name=env["WORKFLOWS_SERVICE_ACCOUNT"],
        image_pull_secrets=["app-pull-secret"],
        artifact_repository_ref=ArtifactRepositoryRef(config_map=env["WORKFLOWS_ARTIFACT_REPOSITORY"]),
        labels={"app.kubernetes.io/name": env["APP_NAME"], "datasets.thinkube.io/dataset": dataset_id,
                "datasets.thinkube.io/version": str(version)},
        ttl_strategy=TTLStrategy(seconds_after_completion=KEEP_SECONDS),
    ) as workflow:
        step = Container(
            name="hub-import",
            image=env["CONTAINER_IMAGE_BACKEND"],
            command=["python", "-m", "app.jobs.hub_import"],
            args=["--experiment", experiment_id, "--run", run_id, "--first-row", str(first_row),
                  "--files", json.dumps(by_split), "--result", RESULT],
            env=[SecretEnv(name=name, secret_name=mlflow_secret, secret_key=key)
                 for name, key in MLFLOW_SECRET_KEYS.items()]
                + [Env(name="DOMAIN_NAME", value=mlflow_settings.DOMAIN_NAME)],
            resources=IMPORT_RESOURCES,
            affinity=NO_GPU,
            outputs=[Parameter(name="result", value_from=ValueFrom(path=RESULT))],
        )
        with Steps(name="import"):
            step(name="hub-import")
    return workflow


def submit_import(dataset_id: str, version: int, experiment_id: str, run_id: str, first_row: int,
                  by_split: Dict[str, List[Dict[str, Any]]]) -> str:
    """Submit the import's workflow and return its name."""
    env = _env()
    workflow = import_workflow(env, dataset_id, version, experiment_id, run_id, first_row, by_split)
    workflow.workflows_service = _service(env)
    try:
        return workflow.create().metadata.name
    except Exception as e:
        raise WorkflowsError(f"Argo did not take the import's workflow: {e}") from e


def state(name: str) -> Optional[WorkflowState]:
    """The workflow's phase, message and result, or None when it no longer exists."""
    env = _env()
    try:
        found = _service(env).get_workflow(name=name, namespace=env["WORKFLOWS_NAMESPACE"])
    except NotFound:
        return None
    except Exception as e:
        raise WorkflowsError(f"Argo did not answer about workflow {name}: {e}") from e
    status = found.status
    # Argo leaves the phase empty until its controller has taken the workflow
    phase = (status.phase if status else None) or "Pending"
    message = status.message if status else None
    result = None
    pods = [n for n in (status.nodes or {}).values() if n.type == "Pod"] if status else []
    if phase in ("Failed", "Error"):
        # The step's own message says more than the workflow's summary
        said = [n.message for n in pods if n.message]
        message = said[-1] if said else message
    if phase == "Succeeded":
        values = [p.value for n in pods if n.outputs and n.outputs.parameters
                  for p in n.outputs.parameters if p.name == "result"]
        if not values:
            raise WorkflowsError(f"workflow {name} succeeded without its result")
        result = json.loads(values[-1])
    return WorkflowState(phase=phase, message=message, result=result)


def delete(name: str) -> None:
    """Cancel the workflow, or remove a finished one; one that is gone already is fine."""
    env = _env()
    try:
        _service(env).delete_workflow(name=name, namespace=env["WORKFLOWS_NAMESPACE"])
    except NotFound:
        return
    except Exception as e:
        raise WorkflowsError(f"Argo did not delete workflow {name}: {e}") from e
