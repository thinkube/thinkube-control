# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The control center's runs: started in this pod as real processes of the
thinkube-mill library, and read from the records they keep."""

import json
import socket
import textwrap
import time

import pytest

from app.services import runs

CLEAN = """
    import time
    from thinkube_mill import task, track, show

    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"}, settings={"pause": 0.0})
    def clean(rows, settings):
        '''Upper-case the text of each row.'''
        print("cleaning starts", flush=True)
        for row in track(rows, "cleaning"):
            time.sleep(settings["pause"])
            out = {"id": row["id"], "text": row["text"].upper()}
            show(input=row, output=out)
            yield out
"""


@pytest.fixture
def work(repository):
    (repository / "demo").mkdir()
    (repository / "demo" / "clean.py").write_text(textwrap.dedent(CLEAN), encoding="utf-8")
    (repository / "data").mkdir()
    (repository / "data" / "rows.jsonl").write_text(
        "".join(json.dumps({"id": i, "text": f"row {i}"}) + "\n" for i in range(12)), encoding="utf-8"
    )
    return {
        "file": "demo/clean.py",
        "inputs": {"rows": "data/rows.jsonl"},
        "outputs": {"cleaned": "data/cleaned.jsonl"},
    }


def wait_for(signed_in, run_id, state, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        answer = signed_in.get(f"/api/v1/runs/{run_id}").json()
        if answer["status"]["state"] == state:
            return answer
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} is {answer['status']['state']}, not {state}: {answer['status']}")


def test_a_run_is_started_followed_and_read(signed_in, work, repository):
    r = signed_in.post("/api/v1/runs", json=work)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]

    answer = wait_for(signed_in, run_id, "completed")
    assert answer["run"]["task"] == "clean"
    assert answer["run"]["runner"] == "mill"
    assert answer["run"]["host"] == socket.gethostname()
    assert answer["has_log"] is True
    [stage] = answer["status"]["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("cleaning", 12, 12)

    cleaned = (repository / "data" / "cleaned.jsonl").read_text().splitlines()
    assert json.loads(cleaned[-1]) == {"id": 11, "text": "ROW 11"}

    listed = signed_in.get("/api/v1/runs").json()
    assert listed["total"] == 1
    assert listed["runs"][0]["id"] == run_id and listed["runs"][0]["state"] == "completed"

    samples = signed_in.get(f"/api/v1/runs/{run_id}/samples").json()
    assert samples["latest"][-1]["fields"] == {"input": {"id": 11, "text": "row 11"},
                                               "output": {"id": 11, "text": "ROW 11"}}

    log = signed_in.get(f"/api/v1/runs/{run_id}/log").json()
    assert "cleaning starts" in log["lines"]

    assert signed_in.get(f"/api/v1/runs/{run_id}/failures").json() == {"failures": [], "total": 0}


def test_limit_and_settings_reach_the_task(signed_in, work):
    r = signed_in.post("/api/v1/runs", json={**work, "limit": 4, "settings": {"pause": 0.01}})
    assert r.status_code == 200, r.text
    answer = wait_for(signed_in, r.json()["id"], "completed")
    assert answer["run"]["limit"] == 4
    assert answer["run"]["settings"] == {"pause": 0.01}
    assert answer["status"]["stages"][0]["total"] == 4


def test_a_task_that_cannot_start_is_refused_with_its_reason(signed_in, work):
    r = signed_in.post("/api/v1/runs", json={**work, "inputs": {}})
    assert r.status_code == 409
    assert "--in rows=<path>" in r.json()["detail"]
    assert signed_in.get("/api/v1/runs").json()["total"] == 0


def test_a_file_outside_the_repository_is_refused(signed_in, work):
    r = signed_in.post("/api/v1/runs", json={**work, "file": "../elsewhere.py"})
    assert r.status_code == 409
    assert "outside the tasks repository" in r.json()["detail"]


def test_a_running_run_is_stopped(signed_in, work, repository):
    r = signed_in.post("/api/v1/runs", json={**work, "settings": {"pause": 5.0}})
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    assert wait_for(signed_in, run_id, "running")["can_stop"] is True

    assert signed_in.post(f"/api/v1/runs/{run_id}/stop").status_code == 200
    answer = wait_for(signed_in, run_id, "stopped")
    assert answer["can_stop"] is False
    assert (repository / "data" / "cleaned.jsonl.partial").exists()
    assert not (repository / "data" / "cleaned.jsonl").exists()

    again = signed_in.post(f"/api/v1/runs/{run_id}/stop")
    assert again.status_code == 409
    assert "only a running run is stopped" in again.json()["detail"]


def test_tasks_of_a_file_are_described(signed_in, work):
    r = signed_in.get("/api/v1/tasks", params={"file": "demo/clean.py"})
    assert r.status_code == 200, r.text
    [described] = r.json()["tasks"]
    assert described["name"] == "clean"
    assert described["description"] == "Upper-case the text of each row."
    assert described["inputs"] == {"rows": "jsonl"}
    assert described["settings"] == {"pause": 0.0}


def test_an_unknown_run_is_404(signed_in):
    assert signed_in.get("/api/v1/runs/20260101-000000-none-0000").status_code == 404


def record(repository, run_id, host, runner="mill", state="running"):
    """A run's record as the library writes it, for a run nothing executes."""
    run_dir = repository / ".mill" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "format": "thinkube-mill/1", "id": run_id, "task": "clean", "file": "demo/clean.py",
        "repository": None, "host": host, "pid": 4242, **({"runner": runner} if runner else {}),
    }))
    (run_dir / "status.json").write_text(json.dumps({
        "format": "thinkube-mill/1", "state": state, "error": None,
        "started_at": "2026-01-01T00:00:00.000+00:00", "updated_at": "2026-01-01T00:00:05.000+00:00",
        "ended_at": None,
        "stages": [{"name": "cleaning", "total": 10, "done": 4,
                    "started_at": "2026-01-01T00:00:00.000+00:00", "ended_at": None}],
        "counters": {}, "measures": {}, "samples": 0, "failures": 0,
    }))
    (run_dir / "samples.json").write_text(json.dumps({"format": "thinkube-mill/1", "latest": [], "spaced": []}))


def test_a_run_this_runner_lost_is_marked_failed(signed_in, repository):
    record(repository, "lost-other-pod", host="mill-backend-gone")
    record(repository, "lost-this-pod", host=socket.gethostname())
    record(repository, "ide-run", host="code-server-1", runner=None)

    states = {run["id"]: run for run in signed_in.get("/api/v1/runs").json()["runs"]}
    assert states["lost-other-pod"]["state"] == "failed"
    assert "mill-backend-gone" in states["lost-other-pod"]["error"]
    assert states["lost-other-pod"]["ended_at"] is not None
    assert states["lost-other-pod"]["stages"][0]["ended_at"] is not None
    assert states["lost-this-pod"]["state"] == "failed"
    # A run something else started is that runner's to judge
    assert states["ide-run"]["state"] == "running"


def test_a_run_started_elsewhere_has_no_log_and_is_not_stopped_here(signed_in, repository):
    record(repository, "ide-run", host="code-server-1", runner=None)
    log = signed_in.get("/api/v1/runs/ide-run/log")
    assert log.status_code == 409 and "started elsewhere" in log.json()["detail"]
    stop = signed_in.post("/api/v1/runs/ide-run/stop")
    assert stop.status_code == 409 and "runs on code-server-1" in stop.json()["detail"]
