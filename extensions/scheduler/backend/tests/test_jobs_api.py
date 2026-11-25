# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Giving the scheduler a job, reading it and stopping it."""

from app.models.jobs import RUNNING, Job

NEW = {"name": "plan of run 7", "owner": "forge", "node": "tkamd2", "image": "library/tk-jupyter-base:latest",
       "command": ["python", "plan.py"], "env": {"TOKEN": "secret"}, "mounts": ["models", "venvs"], "gpus": 1,
       "memory_gb": 32, "cpu_cores": 8}


def test_a_job_is_created_waiting_and_listed_without_its_variables(signed_in):
    made = signed_in.post("/api/v1/jobs", json=NEW)
    assert made.status_code == 200, made.text
    job = made.json()
    assert (job["kind"], job["status"], job["owner"], job["priority"], job["starts"], job["gpu_memory_gb"]) == (
        "job", "waiting", "forge", 3, 0, None)
    assert job["created_by"] == "testuser" and "env" not in job
    listed = signed_in.get("/api/v1/jobs", params={"open_only": True}).json()
    assert [j["id"] for j in listed["jobs"]] == [job["id"]] and listed["worker"] == {"last_tick": None, "error": None}
    assert signed_in.get(f"/api/v1/jobs/{job['id']}").json()["command"] == ["python", "plan.py"]
    assert signed_in.get("/api/v1/jobs/none").status_code == 404


def test_a_job_that_names_an_unknown_folder_or_gpu_memory_without_a_gpu_is_refused(signed_in):
    answer = signed_in.post("/api/v1/jobs", json={**NEW, "mounts": ["home"]})
    assert answer.status_code == 422 and "['home'] are not folders a job mounts" in answer.json()["detail"]
    answer = signed_in.post("/api/v1/jobs", json={**NEW, "gpus": 0, "gpu_memory_gb": 4})
    assert answer.status_code == 422 and "asks for no GPU" in answer.json()["detail"]


def test_a_waiting_job_is_stopped_at_once_and_a_running_one_is_asked_to(signed_in, test_db):
    waiting = signed_in.post("/api/v1/jobs", json=NEW).json()
    assert signed_in.post(f"/api/v1/jobs/{waiting['id']}/stop").json()["status"] == "stopped"
    assert signed_in.post(f"/api/v1/jobs/{waiting['id']}/stop").status_code == 409
    running = signed_in.post("/api/v1/jobs", json=NEW).json()
    test_db.get(Job, running["id"]).status = RUNNING
    test_db.flush()
    assert signed_in.post(f"/api/v1/jobs/{running['id']}/stop").json()["status"] == "stopping"
    # Asked again while it ends: still stopping
    assert signed_in.post(f"/api/v1/jobs/{running['id']}/stop").json()["status"] == "stopping"
    assert signed_in.get(f"/api/v1/jobs/{waiting['id']}/log").json() == {"log": None}


HOLD = {"name": "google/gemma-4-31B-it", "owner": "llm-gateway", "node": "tkamd2", "gpus": 2,
        "release_url": "http://control-llm-backend:8000/internal/llm/holds/release"}


def test_a_service_asks_for_a_hold_without_a_user_and_ends_it(client, test_db):
    made = client.post("/internal/scheduler/holds", json=HOLD)
    assert made.status_code == 200, made.text
    hold = made.json()
    assert (hold["kind"], hold["status"], hold["priority"], hold["created_by"], hold["image"]) == (
        "hold", "waiting", 4, "llm-gateway", None)
    assert client.get(f"/internal/scheduler/jobs/{hold['id']}").json()["release_url"] == HOLD["release_url"]
    # Given its GPUs, it is ended at once when its owner says so: there is no pod of the scheduler's to wait for
    row = test_db.get(Job, hold["id"])
    row.status, row.gpu_ids = RUNNING, ["GPU-0", "GPU-1"]
    test_db.flush()
    ended = client.post(f"/internal/scheduler/jobs/{hold['id']}/stop").json()
    assert (ended["status"], ended["gpu_ids"]) == ("stopped", None)
    assert client.post("/internal/scheduler/holds", json={**HOLD, "release_url": "ftp://x"}).status_code == 422


def test_a_hold_is_released_only_when_it_was_asked_to_give_its_gpus_up(signed_in, test_db):
    hold = signed_in.post("/api/v1/holds", json=HOLD).json()
    row = test_db.get(Job, hold["id"])
    row.status, row.gpu_ids = RUNNING, ["GPU-0", "GPU-1"]
    test_db.flush()
    refused = signed_in.post(f"/api/v1/jobs/{hold['id']}/release")
    assert refused.status_code == 409 and "was not asked to give its GPUs up" in refused.json()["detail"]
    row.status, row.preempted_by = "stopping", "some-job"
    test_db.flush()
    released = signed_in.post(f"/api/v1/jobs/{hold['id']}/release").json()
    assert (released["status"], released["gpu_ids"]) == ("waiting", None)
    job = signed_in.post("/api/v1/jobs", json=NEW).json()
    assert signed_in.post(f"/api/v1/jobs/{job['id']}/release").status_code == 409
