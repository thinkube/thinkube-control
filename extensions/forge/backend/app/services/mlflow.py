# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Reading a run's progress from MLflow, where the training logs it.

The training notebook tags its MLflow run with forge.run_id; the metrics it
logs are the Hugging Face trainer's (loss, learning_rate, grad_norm, epoch).
Forge reads them with the MLflow service account the platform gives every
app."""

from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

EXPERIMENT = "forge"
METRICS = ("loss", "eval_loss", "learning_rate", "grad_norm", "epoch")
TIMEOUT = httpx.Timeout(30.0)


class MlflowError(Exception):
    """MLflow refused or failed a call; the message says which."""


async def _token(client: httpx.AsyncClient) -> str:
    r = await client.post(settings.MLFLOW_KEYCLOAK_TOKEN_URL, data={
        "grant_type": "password",
        "client_id": settings.MLFLOW_KEYCLOAK_CLIENT_ID,
        "client_secret": settings.MLFLOW_CLIENT_SECRET,
        "username": settings.MLFLOW_AUTH_USERNAME,
        "password": settings.MLFLOW_AUTH_PASSWORD,
        "scope": "openid",
    })
    if r.status_code >= 400:
        raise MlflowError(f"Keycloak refused the MLflow service account: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


async def _call(client: httpx.AsyncClient, token: str, method: str, path: str, **kwargs) -> Dict[str, Any]:
    r = await client.request(method, f"{settings.MLFLOW_URL}/api/2.0/mlflow/{path}",
                             headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code >= 400:
        raise MlflowError(f"MLflow answered {method} {path} with {r.status_code}: {r.text[:300]}")
    return r.json()


# The values MLflow returns in one page of a metric's history; a training logs one loss a step, so a run of more
# steps than this spans several pages
HISTORY_PAGE = 25000


async def _history(client: httpx.AsyncClient, token: str, run_id: str, metric: str) -> List[Dict[str, Any]]:
    """Every value MLflow holds for the metric, page after page."""
    found: List[Dict[str, Any]] = []
    params = {"run_id": run_id, "metric_key": metric, "max_results": HISTORY_PAGE}
    while True:
        page = await _call(client, token, "GET", "metrics/get-history", params=params)
        found += page.get("metrics", [])
        if not page.get("next_page_token"):
            return found
        params = {**params, "page_token": page["next_page_token"]}


def by_step(values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One value a step, in the order of the steps. A resumed training runs again the steps after its checkpoint
    and logs them again; the value logged last is the one the training went on from."""
    latest: Dict[int, Dict[str, Any]] = {}
    for m in values:
        step = int(m["step"])
        if step not in latest or int(m["timestamp"]) >= int(latest[step]["timestamp"]):
            latest[step] = m
    return [{"step": step, "value": latest[step]["value"]} for step in sorted(latest)]


async def progress(forge_run_id: str) -> Optional[Dict[str, Any]]:
    """The MLflow run of a Forge run with its metric histories, or None
    while the training has not started its MLflow run."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await _token(client)
            experiment = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow/experiments/get-by-name",
                                          params={"experiment_name": EXPERIMENT},
                                          headers={"Authorization": f"Bearer {token}"})
            if experiment.status_code == 404:
                return None
            if experiment.status_code >= 400:
                raise MlflowError(f"MLflow answered experiments/get-by-name with {experiment.status_code}")
            experiment_id = experiment.json()["experiment"]["experiment_id"]
            found = await _call(client, token, "POST", "runs/search", json={
                "experiment_ids": [experiment_id],
                "filter": f"tags.`forge.run_id` = '{forge_run_id}'",
                "max_results": 10,
            })
            # The training's run, not an export's
            runs: List[Dict[str, Any]] = [
                r for r in found.get("runs", [])
                if not any(t["key"] == "forge.export" for t in r.get("data", {}).get("tags", []))
            ]
            if not runs:
                return None
            info = runs[0]["info"]
            histories = {}
            for metric in METRICS:
                histories[metric] = by_step(await _history(client, token, info["run_id"], metric))
        except httpx.HTTPError as e:
            raise MlflowError(f"MLflow did not answer: {e}")
    return {
        "mlflow_run_id": info["run_id"],
        "status": info["status"],
        "url": f"{settings.MLFLOW_URL}/#/experiments/{experiment_id}/runs/{info['run_id']}",
        "metrics": histories,
    }


async def model_file(model_id: str, name: str) -> Optional[str]:
    """A text file of a registered model's latest version, or None when the model has no such file."""
    registered = model_id.replace("/", "-")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await _token(client)
            found = await _call(client, token, "GET", "model-versions/search", params={"filter": f"name='{registered}'"})
            versions = found.get("model_versions", [])
            if not versions:
                raise MlflowError(f"{model_id} is not in the MLflow registry; mirror it in Thinkube Control first")
            latest = max(versions, key=lambda v: int(v["version"]))
            run = await _call(client, token, "GET", "runs/get", params={"run_id": latest["run_id"]})
            experiment = run["run"]["info"]["experiment_id"]
            folder = f"{experiment}/{latest['run_id']}/artifacts/model"
            path = f"{folder}/{name}"
            # MLflow answers 500, not 404, for a file that is not there: the listing says first
            listed = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts",
                                      params={"path": folder}, headers={"Authorization": f"Bearer {token}"})
            if listed.status_code >= 400:
                raise MlflowError(f"MLflow answered the listing of {folder} with {listed.status_code}")
            if name not in {f.get("path") for f in listed.json().get("files", [])}:
                return None
            r = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts/{path}",
                                 headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise MlflowError(f"MLflow did not answer: {e}")
    if r.status_code >= 400:
        raise MlflowError(f"MLflow answered the download of {path} with {r.status_code}")
    return r.text
