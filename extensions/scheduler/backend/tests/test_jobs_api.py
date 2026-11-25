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
    other = client.post("/internal/scheduler/holds", json={**HOLD, "name": "another model"}).json()
    client.post("/internal/scheduler/holds", json={**HOLD, "owner": "someone-else"})
    # An owner reads its holds that have not ended
    listed = client.get("/internal/scheduler/jobs", params={"owner": "llm-gateway"}).json()["jobs"]
    assert [j["id"] for j in listed] == [other["id"]]
    assert client.post("/internal/scheduler/holds", json={**HOLD, "release_url": "ftp://x"}).status_code == 422
    # A hold may say the namespace of its pod
    server = client.post("/internal/scheduler/holds", json={**HOLD, "owner": "jupyterhub", "release_url": None,
                                                            "pods_in": "jupyterhub"}).json()
    assert (server["pods_in"], server["pod_seen_at"]) == ("jupyterhub", None)
    assert client.post("/internal/scheduler/holds", json={**HOLD, "pods_in": "Not A Namespace"}).status_code == 422


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


def test_a_holds_memory_is_changed_and_a_growth_its_gpu_does_not_hold_is_refused(signed_in, test_db, monkeypatch):
    from app.services import nodes

    GIB = 2**30

    class Fake:
        async def list(self):
            return [{"name": "tkamd2", "kind": "separate", "memory_available_bytes": 100 * GIB,
                     "gpus": [{"id": "GPU-0", "index": 0, "memory_total_bytes": 24 * GIB,
                               "memory_used_bytes": used["gib"] * GIB}]}]

    # What the card reports in use: 10 GiB of other work, and what the hold's pod has taken
    used = {"gib": 16}
    monkeypatch.setattr(nodes, "nodes", Fake())
    pod = signed_in.post("/api/v1/holds", json={**HOLD, "name": "heads-tkamd2", "gpus": 1, "gpu_memory_gb": 6}).json()
    # Waiting, it asks for what it will need
    assert signed_in.post(f"/api/v1/jobs/{pod['id']}/memory", json={"gpu_memory_gb": 8}).json()["gpu_memory_gb"] == 8
    row = test_db.get(Job, pod["id"])
    row.status, row.gpu_ids, row.gpu_memory_gb = RUNNING, ["GPU-0"], 6
    test_db.flush()
    # The card reports 16 GiB in use, 6 of them the hold's: 22.8 less the other 10 hold up to 12.8
    assert signed_in.post(f"/api/v1/jobs/{pod['id']}/memory", json={"gpu_memory_gb": 12}).json()["gpu_memory_gb"] == 12
    used["gib"] = 22
    refused = signed_in.post(f"/internal/scheduler/jobs/{pod['id']}/memory", json={"gpu_memory_gb": 14})
    assert refused.status_code == 409
    assert refused.json()["detail"] == ("hold heads-tkamd2 cannot take 14 GiB on its GPUs: waiting for a GPU of tkamd2 "
                                        "(14.0 GiB on a GPU): GPU 0 has 12.8 GiB free")
    assert test_db.get(Job, pod["id"]).gpu_memory_gb == 12
    # It shrinks without a question
    assert signed_in.post(f"/api/v1/jobs/{pod['id']}/memory", json={"gpu_memory_gb": 3}).json()["gpu_memory_gb"] == 3
    alone = signed_in.post("/api/v1/holds", json=HOLD).json()
    assert signed_in.post(f"/api/v1/jobs/{alone['id']}/memory", json={"gpu_memory_gb": 3}).status_code == 409


def test_a_part_of_the_platform_gives_a_job_without_a_user(client):
    made = client.post("/internal/scheduler/jobs", json={
        "name": "forge run x: train", "owner": "forge", "node": "tkamd2", "image": "library/tk-jupyter-base:latest",
        "command": ["python", "-c", "print(1)"], "mounts": ["models", "mlflow", "venvs"], "gpus": 2,
        "memory_gb": 60, "cpu_cores": 8, "priority": 3, "resumable": True, "stop_seconds": 300})
    assert made.status_code == 200, made.text
    job = made.json()
    assert (job["kind"], job["owner"], job["created_by"], job["status"], job["resumable"]) == (
        "job", "forge", "forge", "waiting", True)
    assert client.get(f"/internal/scheduler/jobs/{job['id']}/log").json() == {"log": None}
    refused = client.post("/internal/scheduler/jobs", json={
        "name": "x", "owner": "forge", "node": "tkamd2", "image": "i", "command": ["true"], "mounts": ["home"],
        "memory_gb": 1, "cpu_cores": 1})
    assert refused.status_code == 422 and "not folders a job mounts" in refused.text


def test_a_person_sets_the_priority_of_a_kind_of_work_and_the_platform_reads_it(signed_in, client):
    listed = signed_in.get("/api/v1/priorities").json()
    assert [(k["kind"], k["priority"], k["default"]) for k in listed] == [
        ("notebook_server", 1, 1), ("notebook_job", 2, 2), ("forge_run", 3, 3), ("served_model", 4, 4), ("app", 5, 5)]
    changed = signed_in.put("/api/v1/priorities", json={"served_model": 3}).json()
    assert next(k for k in changed if k["kind"] == "served_model")["priority"] == 3
    assert client.get("/internal/scheduler/priorities").json() == {
        "notebook_server": 1, "notebook_job": 2, "forge_run": 3, "served_model": 3, "app": 5}
    for refused, why in (({"served_model": 6}, "from 1"), ({"printer": 2}, "not kinds of work")):
        answer = signed_in.put("/api/v1/priorities", json=refused)
        assert answer.status_code == 422 and why in answer.text
