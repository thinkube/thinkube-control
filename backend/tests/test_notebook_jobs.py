# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An unattended run ends only on a final status or a lost server, is followed
again after a restart while its server exists, and a training needs training
mode on for its node."""

import asyncio
import uuid

import pytest
from fastapi import HTTPException

import app.api.jupyter_servers as js

JOB = uuid.uuid4()


class Jobs:
    """The notebook_jobs row of one job, as _read_job and _update_job see it."""

    def __init__(self, status="running"):
        self.row = {"job_id": str(JOB), "status": status}
        self.updates = []

    def read(self, job_id):
        return dict(self.row)

    def update(self, job_id, **fields):
        self.updates.append(fields)
        self.row.update(fields)
        return dict(self.row)


@pytest.fixture
def jobs(monkeypatch):
    j = Jobs()
    monkeypatch.setattr(js, "_read_job", j.read)
    monkeypatch.setattr(js, "_update_job", j.update)
    monkeypatch.setattr(js, "JOB_POLL_SECONDS", 0)
    return j


def answers(monkeypatch, *replies):
    """call_tool gives these replies in order; an exception in the list is raised."""
    queue = list(replies)

    async def call_tool(tool, arguments, timeout=60.0, server_name=None, node=None):
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(js.jupyter_notebooks, "call_tool", call_tool)


class Hub:
    HubError = js.hub.HubError

    def __init__(self, model=None, errors=0):
        self.model = model
        self.errors = errors

    async def server(self, name, include_stopped=False):
        if self.errors:
            self.errors -= 1
            raise js.hub.HubError("JupyterHub is not reachable")
        return self.model

    async def wait_ready(self, name, timeout=240.0, interval=3.0):
        return {"ready": True}


def test_a_slow_status_answer_does_not_end_the_run(monkeypatch, jobs):
    monkeypatch.setattr(js, "hub", Hub(model={"ready": True}))
    answers(
        monkeypatch,
        HTTPException(status_code=504, detail="The notebook server did not answer 'check_all_cells_status' within 60 seconds."),
        {"success": True, "status": "running", "completed_cells": 3, "total_cells": 9},
        {"success": False, "error": "could not read the record"},
        {"success": True, "status": "completed", "completed_cells": 9, "total_cells": 9},
    )

    asyncio.run(js._poll_job(JOB, "job-1", "exec-1"))

    assert jobs.row["status"] == "completed"
    assert [u.get("status") for u in jobs.updates] == [None, "completed"]


def test_a_failed_status_call_on_a_server_the_hub_no_longer_lists_ends_the_run_as_lost(monkeypatch, jobs):
    monkeypatch.setattr(js, "hub", Hub(model=None))
    answers(monkeypatch, HTTPException(status_code=503, detail="No notebook server is running."))

    asyncio.run(js._poll_job(JOB, "job-1", "exec-1"))

    assert jobs.row["status"] == "lost"
    assert "job-1 is gone" in jobs.row["error"]


def test_a_hub_that_does_not_answer_is_not_a_lost_server(monkeypatch, jobs):
    monkeypatch.setattr(js, "hub", Hub(model={"ready": True}, errors=1))
    answers(
        monkeypatch,
        HTTPException(status_code=502, detail="could not be reached"),
        {"success": False, "status": "error", "error": "cell 2 raised", "failed_cell_index": 2},
    )

    asyncio.run(js._poll_job(JOB, "job-1", "exec-1"))

    assert jobs.row["status"] == "error"
    assert jobs.row["error"] == "cell 2 raised"


def resume(monkeypatch, jobs, hub, record):
    monkeypatch.setattr(js, "hub", hub)
    followed, opened, removed = [], [], []

    async def poll(job_id, server_name, execution_id):
        followed.append(execution_id)
        jobs.update(job_id, status="completed")

    async def open_and_run(job_id, server_name, notebook_path, kernel_name, needs_gpu, stop_on_error, cell_timeout):
        opened.append((notebook_path, needs_gpu, stop_on_error, cell_timeout))
        jobs.update(job_id, status="completed")

    async def remove(job_id, server_name):
        removed.append(server_name)

    monkeypatch.setattr(js, "_poll_job", poll)
    monkeypatch.setattr(js, "_open_and_run", open_and_run)
    monkeypatch.setattr(js, "_remove_job_server", remove)
    asyncio.run(js._resume_job({"job_id": str(JOB), "server_name": "job-1", **record}))
    return followed, opened, removed


def test_a_starting_job_whose_server_is_ready_is_followed_again(monkeypatch, jobs):
    jobs.row["status"] = "starting"
    followed, opened, removed = resume(
        monkeypatch, jobs, Hub(model={"ready": True}),
        {"status": "starting", "execution_id": "exec-1"},
    )
    assert followed == ["exec-1"] and opened == []
    assert removed == ["job-1"]


def test_a_job_whose_run_was_not_started_starts_it_on_its_server(monkeypatch, jobs):
    jobs.row["status"] = "starting"
    followed, opened, _ = resume(
        monkeypatch, jobs, Hub(model={"ready": False, "pending": "spawn"}),
        {"status": "starting", "execution_id": None, "notebook_path": "train.ipynb", "gpus": 1,
         "stop_on_error": True, "cell_timeout_seconds": None},
    )
    assert followed == []
    assert opened == [("train.ipynb", True, True, None)]


def test_a_job_whose_server_is_gone_is_marked_lost(monkeypatch, jobs):
    followed, opened, removed = resume(monkeypatch, jobs, Hub(model=None), {"status": "running", "execution_id": "exec-1"})
    assert followed == [] and opened == []
    assert jobs.row["status"] == "lost"
    assert removed == ["job-1"]


def test_a_job_still_open_keeps_its_server(monkeypatch):
    removed = []

    async def remove(job_id, server_name):
        removed.append(server_name)

    monkeypatch.setattr(js, "_read_job", lambda job_id: {"status": "running"})
    monkeypatch.setattr(js, "_remove_job_server", remove)
    asyncio.run(js._release_job(JOB, "job-1"))
    assert removed == []


def ask_training(monkeypatch, record, node="tkspark"):
    async def status():
        return record

    monkeypatch.setattr(js.training_mode, "status", status)
    request = js.RunNotebookJobRequest(notebook_path="train.ipynb", node=node, training=True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(js.run_notebook_job(request, current_user={}, db=None))
    return e.value


def test_a_training_is_refused_while_training_mode_is_off(monkeypatch):
    e = ask_training(monkeypatch, None)
    assert e.status_code == 409
    assert "turn training mode on for tkspark first" in e.detail


def test_a_training_is_refused_on_another_node_than_training_mode(monkeypatch):
    e = ask_training(monkeypatch, {"node": "tkamd2"})
    assert e.status_code == 409
    assert "training mode is on for tkamd2, not tkspark" in e.detail
