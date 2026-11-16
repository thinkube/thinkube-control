# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

import json
import pathlib
import textwrap

import pytest

from thinkube_mill import _run


@pytest.fixture(autouse=True)
def write_every_change(monkeypatch):
    """The status is sent at every change, so a test reads what the task just reported."""
    monkeypatch.setattr(_run, "WRITE_INTERVAL", 0)


class Sent(list):
    """The messages a run sent, in order, as (kind, payload)."""

    def __call__(self, kind, payload):
        # As a message on NATS would be: JSON
        self.append((kind, json.loads(json.dumps(payload, default=str))))

    def of(self, kind):
        return [payload for k, payload in self if k == kind]

    def last(self, kind):
        return self.of(kind)[-1]


@pytest.fixture
def workspace(tmp_path):
    """A folder with a task file and an input of 30 rows, and a way to run a task of it."""
    from thinkube_mill import _execute

    class Workspace:
        root = tmp_path
        rows = tmp_path / "rows.jsonl"
        cleaned = tmp_path / "out" / "cleaned.jsonl"

        def task_file(self, source: str, name: str = "tasks.py") -> pathlib.Path:
            path = tmp_path / name
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            return path

        def read_jsonl(self, path: pathlib.Path) -> list:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        def run(self, file, task=None, inputs=None, outputs=None, settings=None, limit=None):
            """Run a task of a file, with the messages kept in memory. Returns (exit code, messages)."""
            chosen = _execute.pick_task(_execute.load_tasks(file, None), task, file)
            sent = Sent()
            code, _ = _execute.execute(
                file, chosen,
                {"rows": str(self.rows)} if inputs is None else inputs,
                {"cleaned": str(self.cleaned)} if outputs is None else outputs,
                settings or {}, limit, None, "r1", sent,
            )
            return code, sent

    Workspace.rows.write_text(
        "".join(json.dumps({"id": i, "text": "word " * i}) + "\n" for i in range(30)), encoding="utf-8"
    )
    return Workspace()


CLEAN = """
    from thinkube_mill import task, track, show, count, measure, fail

    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"}, settings={"min_words": 5})
    def clean(rows, settings):
        '''Keep the rows with enough words.'''
        for row in track(rows, "cleaning"):
            words = len(row["text"].split())
            measure("words", words)
            if words < settings["min_words"]:
                count("dropped: too short")
                continue
            out = {"id": row["id"], "text": row["text"].strip()}
            show(input=row, output=out)
            yield out
"""
