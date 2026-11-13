# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A mirror job follows its Argo workflow, and a model delete reaches MLflow.

A job whose workflow no longer exists cannot finish; it is failed with a
message, instead of staying "running" for ever. A delete is sent with the
MLflow service user's token, and a refusal is raised with MLflow's answer.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

import app.services.model_downloader as md
from app.services.model_downloader import ModelDownloaderService


def service_answering(answer):
    service = ModelDownloaderService.__new__(ModelDownloaderService)
    if isinstance(answer, Exception):
        def status(name):
            raise answer
    else:
        def status(name):
            return {"workflow_name": name, **answer}
    service.get_download_status = status
    return service


def running_job():
    return SimpleNamespace(model_id="Qwen/Qwen3.5-4B", workflow_name="model-mirror-abc",
                           status="running", error_message=None)


def test_succeeded_workflow_succeeds_the_job():
    job = running_job()
    service_answering({"status": "Succeeded"}).sync_mirror_job(job)
    assert (job.status, job.error_message) == ("succeeded", None)


def test_failed_workflow_fails_the_job_with_its_message():
    job = running_job()
    service_answering({"status": "Failed", "message": "OOMKilled"}).sync_mirror_job(job)
    assert (job.status, job.error_message) == ("failed", "OOMKilled")


def test_missing_workflow_fails_the_job_and_names_it():
    job = running_job()
    service_answering({"status": "NotFound"}).sync_mirror_job(job)
    assert job.status == "failed"
    assert "model-mirror-abc no longer exists" in job.error_message


def test_running_workflow_leaves_the_job_running():
    job = running_job()
    service_answering({"status": "Running"}).sync_mirror_job(job)
    assert (job.status, job.error_message) == ("running", None)


def test_unreadable_argo_raises_and_leaves_the_job():
    job = running_job()
    with pytest.raises(RuntimeError):
        service_answering(RuntimeError("connection refused")).sync_mirror_job(job)
    assert job.status == "running"


# delete_model: the delete is sent to MLflow with a Keycloak token.

MLFLOW_ENV = {
    "MLFLOW_TRACKING_URI": "http://mlflow.test:5000",
    "MLFLOW_KEYCLOAK_TOKEN_URL": "https://auth.test/token",
    "MLFLOW_KEYCLOAK_CLIENT_ID": "mlflow",
    "MLFLOW_CLIENT_SECRET": "s",
    "MLFLOW_AUTH_USERNAME": "u",
    "MLFLOW_AUTH_PASSWORD": "p",
}


def mlflow_answering(monkeypatch, delete_response):
    for k, v in MLFLOW_ENV.items():
        monkeypatch.setenv(k, v)
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "auth.test":
            return httpx.Response(200, json={"access_token": "tok"})
        return delete_response

    real = httpx.AsyncClient
    monkeypatch.setattr(md.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def delete(model_id="Qwen/Qwen3.5-4B"):
    service = ModelDownloaderService.__new__(ModelDownloaderService)
    return asyncio.run(service.delete_model(model_id))


def test_delete_sends_the_registry_name_with_the_token(monkeypatch):
    seen = mlflow_answering(monkeypatch, httpx.Response(200, json={}))
    delete()
    sent = seen[-1]
    assert sent.method == "DELETE"
    assert str(sent.url) == "http://mlflow.test:5000/api/2.0/mlflow/registered-models/delete"
    assert sent.headers["Authorization"] == "Bearer tok"
    assert b'"Qwen-Qwen3.5-4B"' in sent.content


def test_a_model_mlflow_does_not_have_counts_as_deleted(monkeypatch):
    mlflow_answering(monkeypatch, httpx.Response(
        404, json={"error_code": "RESOURCE_DOES_NOT_EXIST"}))
    delete()


def test_a_refused_delete_raises_with_mlflows_answer(monkeypatch):
    mlflow_answering(monkeypatch, httpx.Response(401, text="Authentication required"))
    with pytest.raises(md.MlflowDeleteError, match="HTTP 401: Authentication required"):
        delete()


def test_a_missing_setting_is_named(monkeypatch):
    mlflow_answering(monkeypatch, httpx.Response(200, json={}))
    monkeypatch.delenv("MLFLOW_AUTH_PASSWORD")
    with pytest.raises(md.MlflowDeleteError, match="MLFLOW_AUTH_PASSWORD is not set"):
        delete()
