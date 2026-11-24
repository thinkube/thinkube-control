# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A trained part on a base model, registered from its files: a model version in MLflow and a catalogue entry.

A part (a tagger, a choice head; Meep²'s serving package, meepserve) is a
folder of a few files, its contract meepserve.json among them:

    {"kind": "tagger", "format": 1, "base": "FacebookAI/xlm-roberta-large"}

Registering it writes the files into a new run of the MLflow experiment
model-registry, as a mirror writes a model, under the run's artifact folder
model/, with an MLmodel naming the flavor meepserve, and registers a model
version under the part's id. The catalogue entry (finetuned_models) then
makes the LLM Gateway serve it with the heads backend, task heads, on the
base its entry names, which must be mirrored.

The files are written through MLflow's file system, mounted at
MLFLOW_ROOT: a run's artifact URI mlflow-artifacts:/<x> is the folder
artifacts/<x> there, the folder MLflow serves and the model servers read.
"""

import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

import httpx
import yaml

from app.services.finetuned_models import FinetunedModelError

CONTRACT = "meepserve.json"
EXPERIMENT = "model-registry"
MLFLOW_ROOT = Path("/storage/mlflow")
ARTIFACTS_PREFIX = "mlflow-artifacts:/"
# The files of one part: a tagger's are three of about 60 MB
MAX_FILES = 32


def check_files(names: List[str], contract: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Refuse file names that are not plain, a part without its contract, or a contract whose base is not the
    entry's base_model."""
    if not names or len(names) > MAX_FILES:
        raise FinetunedModelError(f"a part is 1 to {MAX_FILES} files, not {len(names)}", 422)
    for name in names:
        path = PurePosixPath(name)
        if len(path.parts) != 1 or name in (".", "..") or name.startswith("."):
            raise FinetunedModelError(f"a part's file is named plainly, without folders: {name!r}", 422)
    if len(set(names)) != len(names):
        raise FinetunedModelError("a part's files have distinct names", 422)
    if "MLmodel" in names:
        raise FinetunedModelError("a part's files do not include MLmodel: the registration writes it", 422)
    if CONTRACT not in names:
        raise FinetunedModelError(f"a part's files include its contract, {CONTRACT}: its kind, format and base", 422)
    for field, kind in (("kind", str), ("format", int), ("base", str)):
        if not isinstance(contract.get(field), kind) or isinstance(contract.get(field), bool):
            raise FinetunedModelError(f"{CONTRACT}: '{field}' is required, a {kind.__name__}", 422)
    if contract["base"] != entry.get("base_model"):
        raise FinetunedModelError(f"{CONTRACT} names the base {contract['base']}, and the catalogue entry's "
                                  f"base_model is {entry.get('base_model')}", 422)


def artifact_folder(artifact_uri: str, root: Path = MLFLOW_ROOT) -> Path:
    """The folder of a run's artifacts on MLflow's file system."""
    if not artifact_uri.startswith(ARTIFACTS_PREFIX) or artifact_uri.startswith(ARTIFACTS_PREFIX + "/"):
        raise FinetunedModelError(f"the run's artifacts are at {artifact_uri}, not under {ARTIFACTS_PREFIX}", 502)
    return root / "artifacts" / artifact_uri[len(ARTIFACTS_PREFIX):]


def mlmodel(names: List[str], contract: Dict[str, Any], model_id: str) -> str:
    return yaml.safe_dump({
        "flavors": {"meepserve": {"model_files": sorted(names), "kind": contract["kind"],
                                  "format": contract["format"], "base": contract["base"]}},
        "model_id": model_id,
    }, default_flow_style=False)


def registered_name(model_id: str) -> str:
    """The MLflow registered model of a catalogue id, as the mirrors and the model servers name it."""
    return model_id.replace("/", "-")


class Mlflow:
    """The MLflow REST calls a registration makes, with the MLflow service user's token."""

    def __init__(self, url: str, token: str):
        self.url, self.headers = url.rstrip("/"), {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        with httpx.Client(timeout=60) as http:
            resp = http.request(method, f"{self.url}/api/2.0/mlflow/{path}", headers=self.headers, **kwargs)
        if resp.status_code >= 400:
            raise FinetunedModelError(f"MLflow answered {resp.status_code} to {path}: {resp.text[:300]}", 502)
        return resp.json()

    def experiment(self, name: str) -> str:
        with httpx.Client(timeout=60) as http:
            resp = http.get(f"{self.url}/api/2.0/mlflow/experiments/get-by-name", headers=self.headers,
                            params={"experiment_name": name})
        if resp.status_code == 404 or (resp.status_code == 400 and "RESOURCE_DOES_NOT_EXIST" in resp.text):
            return self._call("POST", "experiments/create", json={"name": name})["experiment_id"]
        if resp.status_code >= 400:
            raise FinetunedModelError(f"MLflow answered {resp.status_code} to experiments/get-by-name: "
                                      f"{resp.text[:300]}", 502)
        found = resp.json()["experiment"]
        if found.get("lifecycle_stage") == "deleted":
            self._call("POST", "experiments/restore", json={"experiment_id": found["experiment_id"]})
        return found["experiment_id"]

    def run(self, experiment_id: str, name: str) -> Tuple[str, str]:
        run = self._call("POST", "runs/create", json={"experiment_id": experiment_id, "run_name": name})["run"]
        return run["info"]["run_id"], run["info"]["artifact_uri"]

    def finish(self, run_id: str) -> None:
        self._call("POST", "runs/update", json={"run_id": run_id, "status": "FINISHED"})

    def version(self, name: str, run_id: str) -> str:
        with httpx.Client(timeout=60) as http:
            resp = http.post(f"{self.url}/api/2.0/mlflow/registered-models/create", headers=self.headers,
                             json={"name": name})
        if resp.status_code >= 400 and "RESOURCE_ALREADY_EXISTS" not in resp.text:
            raise FinetunedModelError(f"MLflow answered {resp.status_code} to registered-models/create: "
                                      f"{resp.text[:300]}", 502)
        return self._call("POST", "model-versions/create", json={
            "name": name, "source": f"runs:/{run_id}/model", "run_id": run_id})["model_version"]["version"]


def write_part(mlflow: Mlflow, entry: Dict[str, Any], files: Dict[str, bytes], root: Path = MLFLOW_ROOT) -> Dict:
    """The part's files in a new run of EXPERIMENT and a model version of it; returns where they are."""
    contract = json.loads(files[CONTRACT])
    experiment_id = mlflow.experiment(EXPERIMENT)
    run_id, artifact_uri = mlflow.run(experiment_id, f"part-{registered_name(entry['id'])}")
    folder = artifact_folder(artifact_uri, root) / "model"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)
    (folder / "MLmodel").write_text(mlmodel(list(files), contract, entry["id"]))
    mlflow.finish(run_id)
    version = mlflow.version(registered_name(entry["id"]), run_id)
    return {"run_id": run_id, "version": version, "folder": str(folder)}
