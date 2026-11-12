# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The import's workflow: one small step in Datasets' own image, off the GPU
nodes, naming Datasets' artifact repository and reading MLflow's account
from the app's Secret, never a value."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import workflows

BACKEND = Path(__file__).resolve().parents[1]

ENV = {
    "WORKFLOWS_NAMESPACE": "thinkube-control",
    "WORKFLOWS_SERVER_URL": "http://argo:2746",
    "WORKFLOWS_SERVICE_ACCOUNT": "datasets-workflows",
    "WORKFLOWS_UI_URL": "https://argo/workflows/thinkube-control",
    "WORKFLOWS_ARTIFACT_REPOSITORY": "datasets-artifact-repositories",
    "CONTAINER_IMAGE_BACKEND": "registry/thinkube/datasets-backend:tag",
    "APP_NAME": "datasets",
}


def spec():
    files = {"train": [{"url": "https://hf/train-0.parquet"}]}
    return workflows.import_workflow(ENV, "d1", 1, "3", "r1", 0, files).to_dict()


def test_the_import_runs_as_datasets_in_its_namespace():
    found = spec()
    assert found["metadata"]["namespace"] == "thinkube-control"
    assert found["metadata"]["generateName"] == "datasets-import-"
    assert found["metadata"]["labels"]["datasets.thinkube.io/dataset"] == "d1"
    assert found["spec"]["serviceAccountName"] == "datasets-workflows"
    assert found["spec"]["imagePullSecrets"] == [{"name": "app-pull-secret"}]
    assert found["spec"]["artifactRepositoryRef"] == {"configMap": "datasets-artifact-repositories"}


def test_the_step_is_small_off_the_gpu_nodes_and_holds_no_credential():
    [step] = [t for t in spec()["spec"]["templates"] if t["name"] == "hub-import"]
    container = step["container"]
    assert container["image"] == "registry/thinkube/datasets-backend:tag"
    assert container["command"] == ["python", "-m", "app.jobs.hub_import"]
    assert container["resources"]["limits"] == {"cpu": "1", "memory": "2Gi"}
    [term] = step["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert term["matchExpressions"] == [{"key": "nvidia.com/gpu.count", "operator": "DoesNotExist"}]
    secrets = {e["name"]: e["valueFrom"]["secretKeyRef"] for e in container["env"] if "valueFrom" in e}
    assert secrets["MLFLOW_AUTH_PASSWORD"] == {"name": "datasets-mlflow-credentials", "key": "admin-password"}
    assert all("value" not in e for e in container["env"] if e["name"].startswith("MLFLOW_"))
    assert step["outputs"]["parameters"] == [{"name": "result", "valueFrom": {"path": "/tmp/result.json"}}]


def test_a_missing_variable_is_named(monkeypatch):
    for name in workflows.VARIABLES:
        monkeypatch.setenv(name, ENV[name])
    monkeypatch.delenv("WORKFLOWS_ARTIFACT_REPOSITORY")
    with pytest.raises(workflows.WorkflowsError, match="WORKFLOWS_ARTIFACT_REPOSITORY"):
        workflows.logs_url("w")


def test_the_step_starts_with_only_the_variables_its_workflow_gives_it(tmp_path):
    """The step's pod has MLflow's account and the domain, and no setting of the web app."""
    step_env = {"PATH": os.environ["PATH"], "DOMAIN_NAME": "test.com",
                **{name: "x" for name in workflows.MLFLOW_SECRET_KEYS}}
    found = subprocess.run([sys.executable, "-m", "app.jobs.hub_import", "--help"], cwd=BACKEND, env=step_env,
                           capture_output=True, text=True)
    assert found.returncode == 0, found.stderr
