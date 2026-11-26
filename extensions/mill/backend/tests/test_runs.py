# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The control center's runs: started in this pod as real processes of the
thinkube-mill library, which report through the platform's NATS, and read
from what the consumer stored."""

import datetime
import json
import socket
import textwrap
import time

import pytest

from app.db.session import get_session_local
from app.services import store

CLEAN = """
    import time
    from thinkube_mill import task, track, show, fail

    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"}, settings={"pause": 0.0})
    def clean(rows, settings):
        '''Upper-case the text of each row.'''
        print("cleaning starts", flush=True)
        for row in track(rows, "cleaning"):
            time.sleep(settings["pause"])
            if row["id"] == 7:
                fail("seven is refused", input=row)
                continue
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


def wait_until(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.2)
    raise AssertionError("what the test waited for did not happen")


def test_a_run_is_started_followed_and_read(signed_in, work, repository):
    r = signed_in.post("/api/v1/runs", json=work)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]

    answer = wait_for(signed_in, run_id, "completed")
    assert answer["run"]["task"] == "clean"
    assert answer["run"]["runner"] == "mill"
    assert answer["run"]["host"] == socket.gethostname()
    assert answer["can_stop"] is False
    [stage] = answer["status"]["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("cleaning", 12, 12)

    cleaned = (repository / "data" / "cleaned.jsonl").read_text().splitlines()
    assert json.loads(cleaned[-1]) == {"id": 11, "text": "ROW 11"}

    listed = signed_in.get("/api/v1/runs").json()
    assert listed["total"] == 1 and listed["problem"] is None
    assert listed["runs"][0]["id"] == run_id and listed["runs"][0]["state"] == "completed"
    assert listed["runs"][0]["failures"] == 1

    samples = signed_in.get(f"/api/v1/runs/{run_id}/samples").json()
    assert samples["latest"][-1]["fields"] == {"input": {"id": 11, "text": "row 11"},
                                               "output": {"id": 11, "text": "ROW 11"}}

    failures = signed_in.get(f"/api/v1/runs/{run_id}/failures").json()
    assert failures["total"] == 1
    assert [(f["n"], f["error"], f["fields"]) for f in failures["failures"]] == [
        (1, "seven is refused", {"input": {"id": 7, "text": "row 7"}}),
    ]

    # The last piece of the log is sent as the run's process ends
    log = wait_until(lambda: "cleaning starts" in signed_in.get(f"/api/v1/runs/{run_id}/log").json()["lines"]
                     and signed_in.get(f"/api/v1/runs/{run_id}/log").json())
    assert log["total_lines"] >= 1
    assert signed_in.get(f"/api/v1/runs/{run_id}").json()["has_log"] is True


ASKS = """
    import os
    from thinkube_mill import task

    @task(outputs={"seen": "json"})
    def asks():
        "What a task is given to call a model."
        return {"seen": {"gateway": os.environ.get("LLM_GATEWAY_URL"), "token": os.environ.get("THINKUBE_API_TOKEN")}}
"""


def test_a_run_carries_tags_and_is_found_by_them(signed_in, work):
    first = signed_in.post("/api/v1/runs", json={**work, "tags": {"experiment": "vote-of-finders", "setup": "plain"}})
    assert first.status_code == 200, first.text
    run_id = first.json()["id"]
    wait_for(signed_in, run_id, "completed")
    other = signed_in.post("/api/v1/runs", json=work).json()["id"]
    wait_for(signed_in, other, "completed")

    assert signed_in.get(f"/api/v1/runs/{run_id}").json()["tags"] == {"experiment": "vote-of-finders", "setup": "plain"}
    found = signed_in.get("/api/v1/runs", params={"tag": "experiment=vote-of-finders"}).json()
    assert [r["id"] for r in found["runs"]] == [run_id] and found["total"] == 1
    assert found["runs"][0]["tags"]["setup"] == "plain"
    assert signed_in.get("/api/v1/runs", params={"tag": ["experiment=vote-of-finders", "setup=other"]}).json()["runs"] == []
    assert signed_in.get("/api/v1/runs").json()["total"] == 2

    changed = signed_in.put(f"/api/v1/runs/{other}/tags", json={"tags": {"experiment": "vote-of-finders", "claim": "local-finder"}})
    assert changed.json() == {"id": other, "tags": {"experiment": "vote-of-finders", "claim": "local-finder"}}
    assert signed_in.get("/api/v1/runs", params={"tag": "experiment=vote-of-finders"}).json()["total"] == 2
    removed = signed_in.put(f"/api/v1/runs/{other}/tags", json={"tags": {"claim": ""}})
    assert removed.json()["tags"] == {"experiment": "vote-of-finders"}

    assert signed_in.put(f"/api/v1/runs/{other}/tags", json={"tags": {"Bad Name": "x"}}).status_code == 409
    assert signed_in.put("/api/v1/runs/none/tags", json={"tags": {"a": "b"}}).status_code == 404
    assert signed_in.get("/api/v1/runs", params={"tag": "no-value"}).status_code == 422


def test_a_task_is_given_the_gateway_and_the_callers_token(signed_in, repository):
    (repository / "demo").mkdir()
    (repository / "demo" / "asks.py").write_text(textwrap.dedent(ASKS), encoding="utf-8")
    (repository / "data").mkdir()
    job = {"file": "demo/asks.py", "outputs": {"seen": "data/seen.json"}}
    run_id = signed_in.post("/api/v1/runs", json=job, headers={"Authorization": "Bearer tk_of_the_caller"}).json()["id"]
    answer = wait_for(signed_in, run_id, "completed")
    seen = json.loads((repository / "data" / "seen.json").read_text())
    assert seen == {"gateway": "http://llm-proxy.thinkube-control.svc.cluster.local:8080", "token": "tk_of_the_caller"}
    assert "tk_of_the_caller" not in json.dumps(answer), "the token is not in the run's record"

    without = signed_in.post("/api/v1/runs", json={**job, "outputs": {"seen": "data/seen-2.json"}}).json()["id"]
    wait_for(signed_in, without, "completed")
    assert json.loads((repository / "data" / "seen-2.json").read_text())["token"] is None


SECRET = """
    import os
    from thinkube_mill import task

    @task(outputs={"seen": "json"})
    def secret():
        "Whether a task is given the secret it asked for."
        return {"seen": {"key": os.environ.get("JEV_API_KEY")}}
"""


def test_a_task_gets_the_secrets_it_names_as_environment_variables(signed_in, repository, control_secrets):
    (repository / "demo").mkdir()
    (repository / "demo" / "secret.py").write_text(textwrap.dedent(SECRET), encoding="utf-8")
    (repository / "data").mkdir()
    job = {"file": "demo/secret.py", "outputs": {"seen": "data/seen.json"}, "secrets": ["JEV_API_KEY"]}
    made = signed_in.post("/api/v1/runs", json=job, headers={"Authorization": "Bearer tk_of_the_caller"})
    assert made.status_code == 200, made.text
    answer = wait_for(signed_in, made.json()["id"], "completed")
    assert json.loads((repository / "data" / "seen.json").read_text()) == {"key": "jev-value"}
    # Control is asked with the caller's token, and the run's record does not keep the value
    assert control_secrets["tokens"] == ["Bearer tk_of_the_caller"]
    assert "jev-value" not in json.dumps(answer)


def test_a_run_refuses_a_secret_control_does_not_have_or_that_would_replace_a_variable_of_mill(
        signed_in, work, control_secrets):
    missing = signed_in.post("/api/v1/runs", json={**work, "secrets": ["NOPE"]})
    assert missing.status_code == 422 and "has no secret NOPE" in missing.text
    taken = signed_in.post("/api/v1/runs", json={**work, "secrets": ["LLM_GATEWAY_URL"]})
    assert taken.status_code == 422 and "a variable Mill gives every task" in taken.text
    assert signed_in.get("/api/v1/runs").json()["total"] == 0


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

    stop = signed_in.post(f"/api/v1/runs/{run_id}/stop")
    assert stop.status_code == 200, stop.text
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
    assert signed_in.post("/api/v1/runs/20260101-000000-none-0000/stop").status_code == 404


# -- the store, with messages given by hand -------------------------------------

NOW = datetime.datetime.now(datetime.timezone.utc)


def message(run_id, **content):
    return {"format": "thinkube-mill/2", "run": run_id, **content}


def status(run_id, seq, state="running", done=4):
    return message(
        run_id, seq=seq, state=state, error=None, started_at="2026-01-01T00:00:00.000+00:00",
        updated_at="2026-01-01T00:00:05.000+00:00", ended_at=None,
        stages=[{"name": "cleaning", "total": 10, "done": done,
                 "started_at": "2026-01-01T00:00:00.000+00:00", "ended_at": None}],
        counters={}, measures={}, samples=0, failures=0,
    )


def stored(*messages, at=None):
    """Store messages as the consumer does, each as (kind, payload), stored now unless another time is given."""
    at = at or datetime.datetime.now(datetime.timezone.utc)
    db = get_session_local()()
    try:
        for kind, payload in messages:
            store.apply(db, kind, payload, at)
        db.commit()
    finally:
        db.close()


def started(run_id, host="node-1"):
    return ("started", message(run_id, id=run_id, task="clean", file="demo/clean.py", repository=None,
                               host=host, started_at="2026-01-01T00:00:00.000+00:00"))


def test_messages_become_a_run_and_an_older_or_repeated_one_changes_nothing(signed_in):
    stored(
        started("r1"),
        ("status", status("r1", 1, done=1)),
        ("status", status("r1", 3, done=6)),
        ("status", status("r1", 2, done=3)),
        ("started", message("r1", id="r1", task="other", file="x.py", started_at="2026-02-02T00:00:00.000+00:00")),
        ("samples", message("r1", seq=3, latest=[{"n": 2, "at": "x", "fields": {"output": "b"}}], spaced=[])),
        ("samples", message("r1", seq=1, latest=[{"n": 1, "at": "x", "fields": {"output": "a"}}], spaced=[])),
        ("failure", message("r1", n=1, at="x", error="no text", fields={"input": 1})),
        ("failure", message("r1", n=1, at="x", error="no text", fields={"input": 1})),
        ("log", message("r1", seq=1, text="one\ntwo\n")),
        ("log", message("r1", seq=2, text="three\n")),
        ("log", message("r1", seq=1, text="one\ntwo\n")),
        ("alive", message("r1")),
    )
    answer = signed_in.get("/api/v1/runs/r1").json()
    assert answer["run"]["task"] == "clean"
    assert answer["status"]["stages"][0]["done"] == 6
    assert "seq" not in answer["status"]
    assert signed_in.get("/api/v1/runs/r1/samples").json()["latest"][0]["fields"] == {"output": "b"}
    assert len(signed_in.get("/api/v1/runs/r1/failures").json()["failures"]) == 1
    assert signed_in.get("/api/v1/runs/r1/log").json() == {"lines": ["one", "two", "three"], "total_lines": 3}
    assert signed_in.get("/api/v1/runs/r1/log", params={"lines": 1}).json()["lines"] == ["three"]


def test_a_run_whose_first_status_has_not_arrived_is_not_shown(signed_in):
    stored(started("r1"))
    assert signed_in.get("/api/v1/runs/r1").status_code == 404
    assert signed_in.get("/api/v1/runs").json()["total"] == 0


def test_a_running_run_that_sends_nothing_is_marked_failed(signed_in):
    long_ago = NOW - datetime.timedelta(seconds=store.LOST_AFTER + 30)
    stored(started("silent", host="gpu-node-2"), ("status", status("silent", 1)), at=long_ago)
    stored(started("alive"), ("status", status("alive", 1)))
    stored(started("done"), ("status", status("done", 1, state="completed")), at=long_ago)

    # The consumer marks lost runs while it has nothing to read
    wait_until(lambda: signed_in.get("/api/v1/runs/silent").json()["status"]["state"] == "failed")
    silent = signed_in.get("/api/v1/runs/silent").json()["status"]
    assert "sent nothing for 90 seconds" in silent["error"] and "gpu-node-2" in silent["error"]
    assert silent["ended_at"] is not None and silent["stages"][0]["ended_at"] is not None
    assert signed_in.get("/api/v1/runs/alive").json()["status"]["state"] == "running"
    assert signed_in.get("/api/v1/runs/done").json()["status"]["state"] == "completed"

    # A status that arrives after all replaces the mark
    stored(("status", status("silent", 2, done=9)))
    assert signed_in.get("/api/v1/runs/silent").json()["status"]["state"] == "running"


def test_stopping_a_run_no_process_answers_for_is_refused(signed_in):
    stored(started("gone"), ("status", status("gone", 1)))
    stop = signed_in.post("/api/v1/runs/gone/stop")
    assert stop.status_code == 409
    assert "no process answers for run gone" in stop.json()["detail"]


SLEEP = """
    import time
    from thinkube_mill import task

    @task(outputs={"slept": "json"}, settings={"seconds": 0.0})
    def sleep(settings):
        "Sleep a while, then say so."
        time.sleep(settings["seconds"])
        return {"slept": {"seconds": settings["seconds"]}}
"""


@pytest.fixture
def sleeper(repository):
    (repository / "demo").mkdir()
    (repository / "demo" / "sleep.py").write_text(textwrap.dedent(SLEEP), encoding="utf-8")
    (repository / "data").mkdir()
    return lambda name, seconds: {"file": "demo/sleep.py", "outputs": {"slept": f"data/{name}.json"},
                                  "settings": {"seconds": seconds}}


def test_runs_beyond_the_limit_wait_in_order_and_start_as_earlier_ones_end(signed_in, sleeper, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MILL_MAX_RUNS", 1)

    made = signed_in.post("/api/v1/runs", json=sleeper("first", 3))
    assert made.status_code == 200 and made.json()["state"] == "running", made.text
    first = made.json()
    second = signed_in.post("/api/v1/runs", json={**sleeper("second", 0), "tags": {"experiment": "queue"}}).json()
    third = signed_in.post("/api/v1/runs", json=sleeper("third", 0)).json()
    assert (second["state"], second["position"]) == ("queued", 1)
    assert (third["state"], third["position"]) == ("queued", 2)

    # The list and the run say they wait, and where
    listed = {r["id"]: r for r in signed_in.get("/api/v1/runs").json()["runs"]}
    assert (listed[second["id"]]["state"], listed[second["id"]]["position"]) == ("queued", 1)
    assert (listed[third["id"]]["state"], listed[third["id"]]["position"]) == ("queued", 2)
    assert listed[first["id"]]["position"] is None
    detail = signed_in.get(f"/api/v1/runs/{third['id']}").json()
    assert detail["status"]["state"] == "queued" and detail["status"]["position"] == 2 and detail["can_stop"]
    assert signed_in.get("/api/v1/runs", params={"tag": "experiment=queue"}).json()["runs"][0]["id"] == second["id"]

    # Each starts once the one before it has ended, in order, under the id it was given, with its tags
    wait_for(signed_in, first["id"], "completed")
    ended = {r: wait_for(signed_in, r, "completed", seconds=60) for r in (second["id"], third["id"])}
    assert ended[second["id"]]["tags"] == {"experiment": "queue"}
    assert ended[second["id"]]["status"]["started_at"] >= wait_for(signed_in, first["id"], "completed")["status"]["ended_at"]
    assert ended[third["id"]]["status"]["started_at"] >= ended[second["id"]]["status"]["ended_at"]
    assert signed_in.get("/api/v1/runs").json()["total"] == 3


def test_a_queued_run_is_cancelled_and_never_starts(signed_in, sleeper, repository, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MILL_MAX_RUNS", 1)

    first = signed_in.post("/api/v1/runs", json=sleeper("first", 3)).json()
    waiting = signed_in.post("/api/v1/runs", json=sleeper("waiting", 0)).json()
    assert waiting["state"] == "queued"
    cancelled = signed_in.post(f"/api/v1/runs/{waiting['id']}/stop")
    assert cancelled.json() == {"id": waiting["id"], "state": "stopped"}, cancelled.text
    detail = signed_in.get(f"/api/v1/runs/{waiting['id']}").json()
    assert detail["status"]["state"] == "stopped" and detail["status"]["error"] == "cancelled before it started"
    assert not detail["can_stop"]
    assert signed_in.post(f"/api/v1/runs/{waiting['id']}/stop").status_code == 409

    wait_for(signed_in, first["id"], "completed")
    time.sleep(3)
    assert signed_in.get(f"/api/v1/runs/{waiting['id']}").json()["status"]["state"] == "stopped"
    assert not (repository / "data" / "waiting.json").exists()


def test_a_run_a_restarted_pod_left_in_the_queue_ends_stopped_with_the_reason(signed_in):
    import datetime

    from app.models.runs import MillQueuedRun
    from app.services import runs

    session = get_session_local()()
    try:
        session.add(MillQueuedRun(id="left-behind", state="queued", tags={},
                                  queued_at=datetime.datetime.now(datetime.timezone.utc),
                                  request={"file": "demo/sleep.py", "task": None, "inputs": {}, "outputs": {},
                                           "settings": {}, "limit": None, "secrets": []}))
        session.commit()
    finally:
        session.close()
    assert runs.stop_waiting_after_restart() == ["left-behind"]
    detail = signed_in.get("/api/v1/runs/left-behind").json()
    assert detail["status"]["state"] == "stopped" and detail["status"]["error"] == runs.RESTARTED

