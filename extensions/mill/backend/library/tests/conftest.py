# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

import json
import pathlib
import textwrap

import pytest

from thinkube_mill import _run


@pytest.fixture(autouse=True)
def write_every_change(monkeypatch):
    """Records are written at every change, so a test reads what the task just reported."""
    monkeypatch.setattr(_run, "WRITE_INTERVAL", 0)


@pytest.fixture
def workspace(tmp_path):
    """A folder with a task file, an input of 30 rows, and a place for records."""

    class Workspace:
        root = tmp_path
        runs = tmp_path / "runs"
        rows = tmp_path / "rows.jsonl"
        cleaned = tmp_path / "out" / "cleaned.jsonl"

        def task_file(self, source: str, name: str = "tasks.py") -> pathlib.Path:
            path = tmp_path / name
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            return path

        def read_jsonl(self, path: pathlib.Path) -> list:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        def record(self, run_id: str, file: str):
            return json.loads((self.runs / run_id / file).read_text(encoding="utf-8"))

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
