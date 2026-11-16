# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

import json

from thinkube_mill._cli import main

from conftest import CLEAN


def run(workspace, file, *extra, run_id="r1"):
    return main(
        [
            "run", str(file),
            "--in", f"rows={workspace.rows}",
            "--out", f"cleaned={workspace.cleaned}",
            "--runs-dir", str(workspace.runs),
            "--run-id", run_id,
            *extra,
        ]
    )


def test_a_run_writes_its_output_and_its_record(workspace, capsys):
    file = workspace.task_file(CLEAN)
    assert run(workspace, file) == 0

    cleaned = workspace.read_jsonl(workspace.cleaned)
    assert [row["id"] for row in cleaned] == list(range(5, 30))
    assert not workspace.cleaned.with_name("cleaned.jsonl.partial").exists()

    info = workspace.record("r1", "run.json")
    assert info["task"] == "clean"
    assert info["file"] == str(file)
    assert info["settings"] == {"min_words": 5}
    assert info["inputs"]["rows"] == {"type": "jsonl", "path": str(workspace.rows)}

    status = workspace.record("r1", "status.json")
    assert status["state"] == "completed"
    assert status["error"] is None
    assert status["ended_at"] is not None
    [stage] = status["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("cleaning", 30, 30)
    assert stage["ended_at"] is not None
    assert status["counters"] == {"dropped: too short": 5, "written: cleaned": 25}
    assert status["measures"]["words"] == {"count": 30, "sum": 435, "min": 0, "max": 29}
    assert status["samples"] == 25

    samples = workspace.record("r1", "samples.json")
    assert [s["n"] for s in samples["latest"]] == list(range(16, 26))
    assert samples["latest"][-1]["fields"]["output"]["id"] == 29
    assert samples["spaced"][0]["n"] == 1
    assert len(samples["spaced"]) <= 20

    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == "r1" and printed["state"] == "completed"


def test_limit_reads_the_first_items_only(workspace):
    file = workspace.task_file(CLEAN)
    assert run(workspace, file, "--limit", "8") == 0
    assert [row["id"] for row in workspace.read_jsonl(workspace.cleaned)] == [5, 6, 7]
    status = workspace.record("r1", "status.json")
    assert status["stages"][0]["total"] == 8
    assert workspace.record("r1", "run.json")["limit"] == 8


def test_a_setting_is_replaced(workspace):
    file = workspace.task_file(CLEAN)
    assert run(workspace, file, "--set", "min_words=28") == 0
    assert [row["id"] for row in workspace.read_jsonl(workspace.cleaned)] == [28, 29]


def test_a_setting_of_another_kind_is_refused(workspace, capsys):
    file = workspace.task_file(CLEAN)
    assert run(workspace, file, "--set", "min_words=many") == 2
    assert "the setting 'min_words' is a int" in capsys.readouterr().err
    assert not workspace.runs.exists()


def test_a_missing_input_path_is_named(workspace, capsys):
    file = workspace.task_file(CLEAN)
    code = main(["run", str(file), "--out", f"cleaned={workspace.cleaned}", "--runs-dir", str(workspace.runs)])
    assert code == 2
    assert "--in rows=<path>" in capsys.readouterr().err


def test_a_task_that_raises_ends_failed_and_keeps_the_partial_output(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task, track

        @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
        def clean(rows):
            for row in track(rows, "cleaning"):
                if row["id"] == 3:
                    raise ValueError("row 3 is broken")
                yield row
        """
    )
    assert run(workspace, file) == 1
    status = workspace.record("r1", "status.json")
    assert status["state"] == "failed"
    assert status["error"] == "ValueError: row 3 is broken"
    assert status["stages"][0]["done"] == 3
    assert not workspace.cleaned.exists()
    partial = workspace.cleaned.with_name("cleaned.jsonl.partial")
    assert [row["id"] for row in workspace.read_jsonl(partial)] == [0, 1, 2]


def test_a_failed_item_is_recorded_and_the_run_goes_on(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task, track, fail

        @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
        def clean(rows):
            for row in track(rows, "cleaning"):
                if row["id"] % 10 == 0:
                    fail("no text", input=row)
                    continue
                yield row
        """
    )
    assert run(workspace, file) == 0
    status = workspace.record("r1", "status.json")
    assert status["state"] == "completed" and status["failures"] == 3
    failures = workspace.read_jsonl(workspace.runs / "r1" / "failures.jsonl")
    assert [(f["error"], f["fields"]["input"]["id"]) for f in failures] == [
        ("no text", 0), ("no text", 10), ("no text", 20),
    ]


def test_a_yielding_task_without_show_samples_its_items(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task, track

        @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
        def clean(rows):
            for row in track(rows, "cleaning"):
                yield {"id": row["id"], "kept": True}
        """
    )
    assert run(workspace, file) == 0
    last = workspace.record("r1", "samples.json")["latest"][-1]["fields"]
    assert last["input"]["id"] == 29 and last["output"] == {"id": 29, "kept": True}


def test_json_and_text_outputs_come_from_the_returned_dict(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task

        @task(inputs={"rows": "jsonl"}, outputs={"report": "json", "note": "text"})
        def summarise(rows):
            return {"report": {"rows": len(rows)}, "note": "done"}
        """
    )
    report, note = workspace.root / "report.json", workspace.root / "note.txt"
    code = main(
        [
            "run", str(file), "--in", f"rows={workspace.rows}",
            "--out", f"report={report}", "--out", f"note={note}",
            "--runs-dir", str(workspace.runs), "--run-id", "r1",
        ]
    )
    assert code == 0
    assert json.loads(report.read_text()) == {"rows": 30}
    assert note.read_text() == "done"


def test_an_output_the_task_did_not_set_fails_the_run(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task

        @task(inputs={"rows": "jsonl"}, outputs={"report": "json"})
        def summarise(rows):
            return None
        """
    )
    code = main(
        [
            "run", str(file), "--in", f"rows={workspace.rows}",
            "--out", f"report={workspace.root / 'report.json'}",
            "--runs-dir", str(workspace.runs), "--run-id", "r1",
        ]
    )
    assert code == 1
    assert "did not set its output 'report'" in workspace.record("r1", "status.json")["error"]


def test_one_of_several_tasks_is_named(workspace, capsys):
    file = workspace.task_file(
        CLEAN
        + """
    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
    def copy(rows):
        yield from rows
"""
    )
    assert run(workspace, file) == 2
    assert "declares several tasks (clean, copy)" in capsys.readouterr().err
    assert main(
        [
            "run", f"{file}:copy", "--in", f"rows={workspace.rows}",
            "--out", f"cleaned={workspace.cleaned}", "--runs-dir", str(workspace.runs), "--run-id", "r2",
        ]
    ) == 0
    assert len(workspace.read_jsonl(workspace.cleaned)) == 30


def test_describe_prints_the_declarations(workspace, capsys):
    file = workspace.task_file(CLEAN)
    assert main(["describe", str(file)]) == 0
    [described] = json.loads(capsys.readouterr().out)
    assert described["name"] == "clean" and described["settings"] == {"min_words": 5}


def test_status_prints_a_run(workspace, capsys):
    file = workspace.task_file(CLEAN)
    run(workspace, file)
    capsys.readouterr()
    assert main(["status", str(workspace.runs / "r1"), "--samples"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["state"] == "completed" and len(answer["samples_kept"]["latest"]) == 10


def test_a_file_outside_a_repository_needs_a_runs_folder(workspace, capsys, monkeypatch):
    monkeypatch.delenv("MILL_RUNS_DIR", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(workspace.root.parent))
    file = workspace.task_file(CLEAN)
    code = main(["run", str(file), "--in", f"rows={workspace.rows}", "--out", f"cleaned={workspace.cleaned}"])
    assert code == 2
    assert "Give one with --runs-dir, or set MILL_RUNS_DIR" in capsys.readouterr().err
