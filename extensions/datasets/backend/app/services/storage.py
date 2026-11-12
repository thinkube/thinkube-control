# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A dataset's files live in MLflow: one run of the experiment "datasets"
per version of a dataset, with each split at data/<split>.parquet among its
artifacts. A version is a run of its own so that it can be read, kept and
deleted whole, and so that the path of a version's file never changes.

The files go through MLflow's artifact proxy, with the MLflow service
account the platform gives every app. A notebook server mounts the same
artifacts at ~/thinkube/mlflow, so a training reads a split as a file at
artifacts/<experiment>/<run>/artifacts/data/<split>.parquet below it.
"""

from pathlib import Path
from typing import Any, Dict, Tuple

import httpx

from app.core.mlflow import mlflow_settings as settings

EXPERIMENT = "datasets"
TIMEOUT = httpx.Timeout(60.0)
TRANSFER = httpx.Timeout(60.0, read=None, write=None)
PROXY_SCHEME = "mlflow-artifacts:/"


class StorageError(Exception):
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
        raise StorageError(f"Keycloak refused the MLflow service account: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


async def _api(client: httpx.AsyncClient, token: str, method: str, path: str, **kwargs) -> Dict[str, Any]:
    r = await client.request(method, f"{settings.MLFLOW_URL}/api/2.0/mlflow/{path}",
                             headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code >= 400:
        raise StorageError(f"MLflow answered {method} {path} with {r.status_code}: {r.text[:300]}")
    return r.json()


async def _experiment(client: httpx.AsyncClient, token: str) -> str:
    r = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow/experiments/get-by-name",
                         params={"experiment_name": EXPERIMENT}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 404:
        return (await _api(client, token, "POST", "experiments/create", json={"name": EXPERIMENT}))["experiment_id"]
    if r.status_code >= 400:
        raise StorageError(f"MLflow answered experiments/get-by-name with {r.status_code}: {r.text[:300]}")
    return r.json()["experiment"]["experiment_id"]


def artifact_root(experiment_id: str, run_id: str) -> str:
    """The run's artifacts, below MLflow's proxy root and below ~/thinkube/mlflow/artifacts."""
    return f"{experiment_id}/{run_id}/artifacts"


def split_file(split: str) -> str:
    return f"data/{split}.parquet"


async def create(name: str, dataset_id: str, version: int) -> Tuple[str, str]:
    """A new MLflow run for a version of a dataset: (experiment id, run id)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await _token(client)
            experiment_id = await _experiment(client, token)
            run = await _api(client, token, "POST", "runs/create", json={
                "experiment_id": experiment_id,
                "run_name": f"{name} v{version}",
                "tags": [{"key": "datasets.id", "value": dataset_id}, {"key": "datasets.version", "value": str(version)},
                         {"key": "mlflow.runName", "value": f"{name} v{version}"}],
            })
        except httpx.HTTPError as e:
            raise StorageError(f"MLflow did not answer: {e}")
    info = run["run"]["info"]
    if not info["artifact_uri"].startswith(PROXY_SCHEME):
        raise StorageError(f"the experiment {EXPERIMENT} keeps artifacts at {info['artifact_uri']}, not through the proxy")
    return experiment_id, info["run_id"]


def _proxy(path: str) -> str:
    return f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts/{path}"


async def _chunks(local: Path, size: int = 8 * 1024 * 1024):
    with local.open("rb") as f:
        while chunk := f.read(size):
            yield chunk


async def upload(experiment_id: str, run_id: str, split: str, local: Path) -> None:
    path = f"{artifact_root(experiment_id, run_id)}/{split_file(split)}"
    async with httpx.AsyncClient(timeout=TRANSFER) as client:
        try:
            token = await _token(client)
            # MLflow's proxy stores nothing from a chunked body: the length goes first
            r = await client.put(_proxy(path), content=_chunks(local), headers={
                "Authorization": f"Bearer {token}", "Content-Length": str(local.stat().st_size)})
        except httpx.HTTPError as e:
            raise StorageError(f"MLflow did not take {path}: {e}")
    if r.status_code >= 400:
        raise StorageError(f"MLflow answered the upload of {path} with {r.status_code}: {r.text[:300]}")
    await _check_size(path, local.stat().st_size)


async def _listing(parent: str) -> Dict[str, Any]:
    """The files directly below a folder of the artifacts, by name: {name: size or None}."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await _token(client)
            r = await client.get(f"{settings.MLFLOW_URL}/api/2.0/mlflow-artifacts/artifacts",
                                 params={"path": parent}, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise StorageError(f"MLflow did not list {parent}: {e}")
    if r.status_code >= 400:
        raise StorageError(f"MLflow answered the listing of {parent} with {r.status_code}")
    return {f["path"]: f.get("file_size") for f in r.json().get("files", []) if not f.get("is_dir")}


async def _check_size(path: str, expected: int) -> None:
    """The stored file has every byte sent: a short upload is an error, not a dataset."""
    parent, _, name = path.rpartition("/")
    stored = (await _listing(parent)).get(name)
    if stored is None or int(stored) != expected:
        raise StorageError(f"MLflow stored {stored} bytes of {path}; {expected} were sent")


async def download(experiment_id: str, run_id: str, split: str, local: Path) -> None:
    path = f"{artifact_root(experiment_id, run_id)}/{split_file(split)}"
    local.parent.mkdir(parents=True, exist_ok=True)
    partial = local.with_suffix(".part")
    async with httpx.AsyncClient(timeout=TRANSFER) as client:
        try:
            token = await _token(client)
            async with client.stream("GET", _proxy(path), headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status_code >= 400:
                    raise StorageError(f"MLflow answered the download of {path} with {r.status_code}")
                with partial.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise StorageError(f"MLflow did not give {path}: {e}")
    partial.rename(local)


async def delete(experiment_id: str, run_id: str) -> None:
    """Remove every split file of the run, those of an import cut short included, and the run."""
    folder = f"{artifact_root(experiment_id, run_id)}/data"
    files = await _listing(folder)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            token = await _token(client)
            for name in files:
                r = await client.delete(_proxy(f"{folder}/{name}"), headers={"Authorization": f"Bearer {token}"})
                if r.status_code >= 400 and r.status_code != 404:
                    raise StorageError(f"MLflow answered the deletion of {folder}/{name} with {r.status_code}")
            await _api(client, token, "POST", "runs/delete", json={"run_id": run_id})
        except httpx.HTTPError as e:
            raise StorageError(f"MLflow did not answer: {e}")
