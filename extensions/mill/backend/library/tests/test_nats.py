# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The command line, with a run's messages sent through the platform's NATS.

Each test has subjects of its own and a stream for them, as the Mill control
center makes one for "mill", so nothing here reaches the control center.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import nats
import pytest

from thinkube_mill import _report
from thinkube_mill._cli import main

from conftest import CLEAN


async def _with_nats(work):
    nc = await nats.connect(_report.nats_url(), connect_timeout=5)
    try:
        return await work(nc)
    finally:
        await nc.close()


def _messages(prefix):
    """Every message of the test's stream, in order, as (kind, payload)."""

    async def read(nc):
        js = nc.jetstream()
        sub = await js.subscribe(f"{prefix}.runs.>", ordered_consumer=True)
        found = []
        info = await js.stream_info(_report.stream_name(prefix))
        while len(found) < info.state.messages:
            message = await sub.next_msg(timeout=5)
            found.append((message.subject.rsplit(".", 1)[-1], json.loads(message.data)))
        return found

    return asyncio.run(_with_nats(read))


@pytest.fixture
def subjects(monkeypatch):
    prefix = f"milltest-{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("MILL_SUBJECTS", prefix)
    stream = _report.stream_name(prefix)

    async def create(nc):
        await nc.jetstream().add_stream(name=stream, subjects=[f"{prefix}.runs.>"])

    async def delete(nc):
        await nc.jetstream().delete_stream(stream)

    asyncio.run(_with_nats(create))
    yield prefix
    asyncio.run(_with_nats(delete))


def test_a_run_reports_through_nats(workspace, subjects, capsys):
    file = workspace.task_file(
        CLEAN.replace('for row in track(rows, "cleaning"):', 'print("cleaning starts", flush=True)\n        for row in track(rows, "cleaning"):')
    )
    code = main(["run", str(file), "--in", f"rows={workspace.rows}", "--out", f"cleaned={workspace.cleaned}",
                 "--run-id", "r1"])
    assert code == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["id"] == "r1" and printed["state"] == "completed"

    found = _messages(subjects)
    kinds = [kind for kind, _ in found]
    assert kinds[0] == "started"
    assert found[0][1]["run"] == "r1" and found[0][1]["task"] == "clean"
    last_status = [payload for kind, payload in found if kind == "status"][-1]
    assert last_status["state"] == "completed"
    assert last_status["stages"][0]["done"] == 30
    assert "samples" in kinds


def test_a_run_without_its_stream_is_refused(workspace, monkeypatch, capsys):
    monkeypatch.setenv("MILL_SUBJECTS", f"milltest-none-{uuid.uuid4().hex[:8]}")
    file = workspace.task_file(CLEAN)
    code = main(["run", str(file), "--in", f"rows={workspace.rows}", "--out", f"cleaned={workspace.cleaned}"])
    assert code == 2
    assert "has no stream MILLTEST_NONE_" in capsys.readouterr().err
    assert not workspace.cleaned.with_name("cleaned.jsonl.partial").exists()


def test_a_wrong_command_is_said_without_nats(workspace, monkeypatch, capsys):
    monkeypatch.setenv("MILL_NATS_URL", "nats://127.0.0.1:1")
    file = workspace.task_file(CLEAN)
    assert main(["run", str(file), "--out", f"cleaned={workspace.cleaned}"]) == 2
    assert "--in rows=<path>" in capsys.readouterr().err


def test_nats_that_cannot_be_reached_is_said(workspace, monkeypatch, capsys):
    monkeypatch.setenv("MILL_NATS_URL", "nats://127.0.0.1:1")
    file = workspace.task_file(CLEAN)
    code = main(["run", str(file), "--in", f"rows={workspace.rows}", "--out", f"cleaned={workspace.cleaned}"])
    assert code == 2
    assert "cannot connect to NATS at nats://127.0.0.1:1" in capsys.readouterr().err


def test_a_run_is_stopped_by_a_request_and_sends_its_log(workspace, subjects):
    file = workspace.task_file(
        """
        import time
        from thinkube_mill import task, track

        @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
        def slow(rows):
            print("slow starts", flush=True)
            for row in track(rows, "waiting"):
                time.sleep(5)
                yield row
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-m", "thinkube_mill", "run", str(file), "--in", f"rows={workspace.rows}",
         "--out", f"cleaned={workspace.cleaned}", "--run-id", "r2"],
        env={**os.environ}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        async def stop(nc):
            deadline = time.monotonic() + 30
            while True:
                try:
                    answer = await nc.request(f"{subjects}.control.r2.stop", b"", timeout=2)
                    return json.loads(answer.data)
                except (nats.errors.NoRespondersError, nats.errors.TimeoutError):
                    if time.monotonic() > deadline:
                        raise
                    await asyncio.sleep(0.2)

        assert asyncio.run(_with_nats(stop)) == {"stopping": True}
        assert child.wait(timeout=30) == 143
    finally:
        if child.poll() is None:
            child.kill()

    found = _messages(subjects)
    assert [p for k, p in found if k == "status"][-1]["state"] == "stopped"
    log = "".join(p["text"] for k, p in found if k == "log")
    assert "slow starts" in log
    assert workspace.cleaned.with_name("cleaned.jsonl.partial").exists()


def test_describe_prints_the_declarations(workspace, capsys):
    file = workspace.task_file(CLEAN)
    assert main(["describe", str(file)]) == 0
    [described] = json.loads(capsys.readouterr().out)
    assert described["name"] == "clean" and described["settings"] == {"min_words": 5}
