# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The folder of a mirrored model's files on MLflow's file system, which a task reads, found in MLflow with the
MLflow account the platform gives every app."""

import os

import httpx

TIMEOUT = httpx.Timeout(30.0)
SETTINGS = ("MLFLOW_KEYCLOAK_TOKEN_URL", "MLFLOW_KEYCLOAK_CLIENT_ID", "MLFLOW_CLIENT_SECRET",
            "MLFLOW_AUTH_USERNAME", "MLFLOW_AUTH_PASSWORD", "DOMAIN_NAME")


class ModelError(Exception):
    """The model is not mirrored, its files are not there, or MLflow could not be asked; the message says which."""


async def folder(model_id: str) -> str:
    """The folder of a mirrored model's files, relative to the root of MLflow's file system: the "model" artifact
    of the run of its latest registered version."""
    missing = [name for name in SETTINGS if not os.environ.get(name)]
    if missing:
        raise ModelError(f"Mill's backend has no {', '.join(missing)}, which it needs to ask MLflow; deploy Mill again")
    mlflow = f"https://experiments.{os.environ['DOMAIN_NAME']}"
    registered = model_id.replace("/", "-")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            got = await client.post(os.environ["MLFLOW_KEYCLOAK_TOKEN_URL"], data={
                "grant_type": "password", "client_id": os.environ["MLFLOW_KEYCLOAK_CLIENT_ID"],
                "client_secret": os.environ["MLFLOW_CLIENT_SECRET"], "username": os.environ["MLFLOW_AUTH_USERNAME"],
                "password": os.environ["MLFLOW_AUTH_PASSWORD"], "scope": "openid"})
            if got.status_code >= 400:
                raise ModelError(f"Keycloak refused the MLflow account: {got.status_code} {got.text[:300]}")
            headers = {"Authorization": f"Bearer {got.json()['access_token']}"}
            found = await client.get(f"{mlflow}/api/2.0/mlflow/model-versions/search",
                                     params={"filter": f"name='{registered}'"}, headers=headers)
            versions = found.json().get("model_versions", []) if found.status_code < 400 else []
            if not versions:
                raise ModelError(f"{model_id} is not in the MLflow registry; mirror it in Thinkube Control first")
            latest = max(versions, key=lambda v: int(v["version"]))
            run = await client.get(f"{mlflow}/api/2.0/mlflow/runs/get", params={"run_id": latest["run_id"]},
                                   headers=headers)
            if run.status_code >= 400:
                raise ModelError(f"MLflow answered the run of {model_id} with {run.status_code}")
            path = f"{run.json()['run']['info']['experiment_id']}/{latest['run_id']}/artifacts/model"
            listed = await client.get(f"{mlflow}/api/2.0/mlflow-artifacts/artifacts", params={"path": path},
                                      headers=headers)
        except httpx.HTTPError as e:
            raise ModelError(f"MLflow did not answer: {e}")
    if listed.status_code >= 400:
        raise ModelError(f"MLflow answered the listing of {path} with {listed.status_code}")
    if "config.json" not in {f.get("path") for f in listed.json().get("files", [])}:
        raise ModelError(f"{model_id} version {latest['version']} is registered, and {path} holds no config.json; "
                         "its mirror may still be running, or failed")
    return f"artifacts/{path}"
