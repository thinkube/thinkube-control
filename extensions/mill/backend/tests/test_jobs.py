# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A task run on a chosen node: the job Mill gives the Scheduler, with the task's script, its folders, its GPUs and
its priority, and the job's state read back with the runs of its starts."""

from datetime import datetime, timezone

import pytest

from app.models.runs import MillRun
from app.services import scheduled

TASK = {"name": "clean the corpus on tkamd2", "file": "kubexlat/clean.py", "inputs": {"rows": "data/rows.jsonl"},
        "outputs": {"cleaned": "data/cleaned.jsonl"}, "settings": {"strict": True}, "node": "tkamd2", "gpus": 1,
        "memory_gb": 16, "cpu_cores": 4}


@pytest.fixture
def scheduler(monkeypatch):
    """The Scheduler as Mill calls it: the nodes, the priority of Mill tasks, and the jobs given."""
    seen = {"given": [], "stopped": [], "jobs": {}}

    async def nodes():
        return ["tkamd1", "tkamd2", "tkspark"]

    async def priority():
        return 3

    async def give(spec):
        job = {"id": f"s{len(seen['given']) + 1}", "status": "waiting", "waiting": "waiting for a GPU of tkamd2",
               "error": None, "gpu_ids": None, "starts": 0, "priority": spec["priority"]}
        seen["given"].append(spec)
        seen["jobs"][job["id"]] = job
        return dict(job)

    async def get(job_id):
        return dict(seen["jobs"][job_id])

    async def stop(job_id):
        seen["stopped"].append(job_id)
        seen["jobs"][job_id]["status"] = "stopped"
        return dict(seen["jobs"][job_id])

    for name, fn in (("nodes", nodes), ("priority", priority), ("give", give), ("get", get), ("stop", stop)):
        monkeypatch.setattr(scheduled, name, fn)
    return seen


def test_a_task_run_on_a_node_is_a_job_of_the_scheduler(signed_in, scheduler, test_db):
    made = signed_in.post("/api/v1/jobs", json={**TASK, "tags": {"experiment": "x"}})
    assert made.status_code == 200, made.text
    job = made.json()
    assert (job["status"], job["waiting"], job["node"], job["priority"]) == (
        "waiting", "waiting for a GPU of tkamd2", "tkamd2", 3)
    [spec] = scheduler["given"]
    assert (spec["owner"], spec["node"], spec["gpus"], spec["gpu_memory_gb"], spec["priority"]) == (
        "mill", "tkamd2", 1, None, 3)
    assert spec["mounts"] == ["shared"] and spec["image"] == scheduled.TASK_IMAGE and spec["resumable"] is False
    env = spec["env"]
    assert env["MILL_RUN_PREFIX"] == f"mill-{job['id'][:8]}" and env["MILL_STATE_DIR"].endswith(f"{job['id']}/state")
    script = spec["command"][2]
    assert 'RUN_ID="$MILL_RUN_PREFIX-$SCHEDULER_START"' in script
    assert "kubexlat/clean.py --in rows=data/rows.jsonl --out cleaned=data/cleaned.jsonl --set strict=true" in script
    assert '--run-id "$RUN_ID"' in script
    # A start's run appears with the job, as the control center keeps it
    now = datetime.now(timezone.utc)
    test_db.add(MillRun(id=f"mill-{job['id'][:8]}-1", info={}, state="running", status_seq=0, samples_seq=0,
                        started_at=now, seen_at=now))
    test_db.commit()
    [listed] = signed_in.get("/api/v1/jobs").json()["jobs"]
    assert [r["id"] for r in listed["runs"]] == [f"mill-{job['id'][:8]}-1"] and listed["tags"] == {"experiment": "x"}
    stopped = signed_in.post(f"/api/v1/jobs/{job['id']}/stop").json()
    assert stopped["status"] == "stopped" and scheduler["stopped"] == ["s1"]


def test_a_task_run_on_a_node_is_checked_before_it_is_given(signed_in, scheduler):
    nowhere = signed_in.post("/api/v1/jobs", json={**TASK, "node": "tkamd9"})
    assert nowhere.status_code == 422 and "not a node of the cluster" in nowhere.text
    memory_alone = signed_in.post("/api/v1/jobs", json={**TASK, "gpus": 0, "gpu_memory_gb": 4})
    assert memory_alone.status_code == 422 and "asks for no GPU" in memory_alone.text
    urgent = signed_in.post("/api/v1/jobs", json={**TASK, "priority": 1}).json()
    assert urgent["priority"] == 1 and scheduler["given"][-1]["priority"] == 1


def test_a_task_in_a_venv_runs_on_the_notebook_servers_image_with_the_nodes_environments():
    request = {"name": "n", "node": "tkspark", "gpus": 1, "memory_gb": 8, "cpu_cores": 2,
               "task": {"file": "f.py", "venv": "meepsquared"}}
    spec = scheduled.job_spec("j-1", request, {"Qwen/Qwen3-0.6B": "artifacts/3/r/artifacts/model"}, 3, "tok")
    assert spec["image"] == scheduled.VENV_IMAGE and spec["mounts"] == ["shared", "venvs", "mlflow"]
    assert "/var/lib/jupyterhub-venvs/custom/meepsquared/$ARCH/bin/python" in spec["command"][2]
    assert spec["env"]["MILL_MODELS"] == '{"Qwen/Qwen3-0.6B": "/home/thinkube/thinkube/mlflow/artifacts/3/r/artifacts/model"}'
    assert spec["env"]["THINKUBE_API_TOKEN"] == "tok"


@pytest.fixture
def control_secrets(monkeypatch):
    """Thinkube Control's secrets as Mill reads them: their values, by name, with the token they were read with."""
    import httpx

    seen = {"tokens": []}
    values = {"JEV_API_KEY": "jev-value"}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None):
            name = url.rsplit("/", 1)[1]
            seen["tokens"].append((headers or {}).get("Authorization"))
            request = httpx.Request("POST", url)
            if name not in values:
                return httpx.Response(404, json={"detail": "Secret not found"}, request=request)
            return httpx.Response(200, json={"value": values[name]}, request=request)

    monkeypatch.setattr("app.api.jobs.httpx.AsyncClient", Client)
    return seen


def test_a_task_gets_the_secrets_it_names_as_environment_variables(signed_in, scheduler, control_secrets):
    made = signed_in.post("/api/v1/jobs", json={**TASK, "gpus": 0, "secrets": ["JEV_API_KEY"]})
    assert made.status_code == 200, made.text
    [spec] = scheduler["given"]
    assert spec["env"]["JEV_API_KEY"] == "jev-value"
    # The job's record names the secret and does not keep its value
    assert made.json()["request"]["secrets"] == ["JEV_API_KEY"] and "jev-value" not in str(made.json())


def test_a_secret_control_does_not_have_or_that_would_replace_a_variable_of_mill_is_refused(signed_in, scheduler,
                                                                                              control_secrets):
    missing = signed_in.post("/api/v1/jobs", json={**TASK, "secrets": ["NOPE"]})
    assert missing.status_code == 422 and "has no secret NOPE" in missing.text
    taken = signed_in.post("/api/v1/jobs", json={**TASK, "secrets": ["HOME"]})
    assert taken.status_code == 422 and "a variable Mill gives every task" in taken.text
    odd = signed_in.post("/api/v1/jobs", json={**TASK, "secrets": ["jev-key"]})
    assert odd.status_code == 422 and "environment variable's name" in odd.text
    assert scheduler["given"] == []
