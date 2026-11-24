# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A run's files that Forge and its notebook job share: the plan, the status
file and the stop file.

They live among the artifacts of the run's MLflow run, in the experiment
"forge", under forge/. MLflow keeps its artifacts on a JuiceFS volume that
every notebook server mounts at ~/thinkube/mlflow, so a file Forge puts
through MLflow's artifact proxy is a file the notebook reads at
~/thinkube/mlflow/artifacts/<experiment>/<run>/artifacts/forge/. That folder
is MLflow's, and a notebook cannot write into it; the notebook sends its
files through the proxy too, with the MLflow account of the notebook server.

- plan.json: Meep²'s plan, written by the plan job.
- status.json: Meep²'s status file. Meep² writes it on the run's own folder
  (~/thinkube/models/forge/<run>), and the notebook sends each new version
  here while the training runs.
- stop: Forge puts it to stop the training at its next step; Meep²'s
  --stop-file names it on the mount. Forge deletes it before a training
  resumes.
- job.yaml: the Meep² job as the notebook ran it.
"""

import json
from typing import Any, Dict, Optional, Tuple

import httpx

from app.core.config import settings
from app.services import mlflow

EXPERIMENT = mlflow.EXPERIMENT
FOLDER = "forge"
PLAN = "plan.json"
PREPROCESS = "preprocess.json"
STATUS = "status.json"
STOP = "stop"
TIMEOUT = httpx.Timeout(30.0)
PROXY_SCHEME = "mlflow-artifacts:/"


class RunFilesError(Exception):
    """MLflow refused or failed a call about a run's files; the message says which."""


def folder(experiment_id: str, run_id: str) -> str:
    """The run's forge/ folder, below MLflow's proxy root and below ~/thinkube/mlflow/artifacts."""
    return f"{experiment_id}/{run_id}/artifacts/{FOLDER}"


def mount_path(experiment_id: str, run_id: str, name: str) -> str:
    """A file of the run, relative to ~/thinkube/mlflow on a notebook server."""
    return f"artifacts/{folder(experiment_id, run_id)}/{name}"


def _proxy(path: str) -> str:
    return f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts/{path}"


async def create(name: str, forge_run_id: str, dataset: Dict[str, Any]) -> Tuple[str, str]:
    """The MLflow run a Forge run trains into: (experiment id, run id), tagged
    forge.run_id and with the dataset and the version of it the run trains on."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await mlflow._token(client)
            found = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow/experiments/get-by-name",
                                     params={"experiment_name": EXPERIMENT},
                                     headers={"Authorization": f"Bearer {token}"})
            if found.status_code == 404:
                experiment_id = (await mlflow._call(client, token, "POST", "experiments/create",
                                                    json={"name": EXPERIMENT}))["experiment_id"]
            elif found.status_code >= 400:
                raise RunFilesError(f"MLflow answered experiments/get-by-name with {found.status_code}: {found.text[:300]}")
            else:
                experiment_id = found.json()["experiment"]["experiment_id"]
            run = await mlflow._call(client, token, "POST", "runs/create", json={
                "experiment_id": experiment_id,
                "run_name": name,
                "tags": [{"key": "forge.run_id", "value": forge_run_id}, {"key": "mlflow.runName", "value": name},
                         {"key": "datasets.id", "value": dataset["id"]},
                         {"key": "datasets.name", "value": dataset["name"]},
                         {"key": "datasets.version", "value": str(dataset["version"])}],
            })
        except mlflow.MlflowError as e:
            raise RunFilesError(str(e))
        except httpx.HTTPError as e:
            raise RunFilesError(f"MLflow did not answer: {e}")
    info = run["run"]["info"]
    if not info["artifact_uri"].startswith(PROXY_SCHEME):
        raise RunFilesError(f"the experiment {EXPERIMENT} keeps artifacts at {info['artifact_uri']}, not through the proxy")
    return experiment_id, info["run_id"]


async def read_json(experiment_id: str, run_id: str, name: str) -> Optional[Dict[str, Any]]:
    """A JSON file of the run's forge/ folder, or None while it is not there."""
    where = folder(experiment_id, run_id)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await mlflow._token(client)
            headers = {"Authorization": f"Bearer {token}"}
            # MLflow answers 500, not 404, for a file that is not there: the listing says first
            listed = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts",
                                      params={"path": where}, headers=headers)
            if listed.status_code >= 400:
                raise RunFilesError(f"MLflow answered the listing of {where} with {listed.status_code}")
            if name not in {f.get("path") for f in listed.json().get("files", [])}:
                return None
            r = await client.get(_proxy(f"{where}/{name}"), headers=headers)
        except mlflow.MlflowError as e:
            raise RunFilesError(str(e))
        except httpx.HTTPError as e:
            raise RunFilesError(f"MLflow did not answer: {e}")
    if r.status_code >= 400:
        raise RunFilesError(f"MLflow answered the download of {where}/{name} with {r.status_code}")
    try:
        return json.loads(r.text)
    except ValueError as e:
        raise RunFilesError(f"{where}/{name} is not JSON: {e}")


async def _send(method: str, experiment_id: str, run_id: str, name: str, content: bytes = b"") -> None:
    path = f"{folder(experiment_id, run_id)}/{name}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await mlflow._token(client)
            headers = {"Authorization": f"Bearer {token}"}
            if method == "PUT":
                # MLflow's proxy stores nothing from a chunked body: the length goes first
                headers["Content-Length"] = str(len(content))
                r = await client.put(_proxy(path), content=content, headers=headers)
            else:
                r = await client.delete(_proxy(path), headers=headers)
        except mlflow.MlflowError as e:
            raise RunFilesError(str(e))
        except httpx.HTTPError as e:
            raise RunFilesError(f"MLflow did not answer {method} {path}: {e}")
    if r.status_code >= 400 and not (method == "DELETE" and r.status_code == 404):
        raise RunFilesError(f"MLflow answered {method} {path} with {r.status_code}: {r.text[:300]}")


class RunFiles:
    """The files of a Forge run, by its Run row."""

    @staticmethod
    def _of(run) -> Tuple[str, str]:
        if not (run.mlflow_experiment_id and run.mlflow_run_id):
            raise RunFilesError(f"run {run.name} has no MLflow run to keep its files in")
        return run.mlflow_experiment_id, run.mlflow_run_id

    async def plan(self, run) -> Optional[Dict[str, Any]]:
        return await read_json(*self._of(run), PLAN)

    async def preprocess(self, run) -> Optional[Dict[str, Any]]:
        return await read_json(*self._of(run), PREPROCESS)

    async def status(self, run) -> Optional[Dict[str, Any]]:
        return await read_json(*self._of(run), STATUS)

    async def request_stop(self, run) -> None:
        await _send("PUT", *self._of(run), STOP, b"stop\n")

    async def clear_stop(self, run) -> None:
        await _send("DELETE", *self._of(run), STOP)
