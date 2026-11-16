# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""What a run does and what it sends. The messages are kept in memory here;
test_nats.py sends them through NATS."""

import json

import pytest

from thinkube_mill import TaskError

from conftest import CLEAN


def test_a_run_writes_its_output_and_sends_its_record(workspace):
    file = workspace.task_file(CLEAN)
    code, sent = workspace.run(file)
    assert code == 0

    cleaned = workspace.read_jsonl(workspace.cleaned)
    assert [row["id"] for row in cleaned] == list(range(5, 30))
    assert not workspace.cleaned.with_name("cleaned.jsonl.partial").exists()

    assert sent[0][0] == "started"
    info = sent[0][1]
    assert info["id"] == "r1" and info["task"] == "clean"
    assert info["file"] == str(file)
    assert info["settings"] == {"min_words": 5}
    assert info["inputs"]["rows"] == {"type": "jsonl", "path": str(workspace.rows)}

    assert sent[-1][0] == "status"
    status = sent.last("status")
    assert status["state"] == "completed"
    assert status["error"] is None
    assert status["ended_at"] is not None
    [stage] = status["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("cleaning", 30, 30)
    assert stage["ended_at"] is not None
    assert status["counters"] == {"dropped: too short": 5, "written: cleaned": 25}
    assert status["measures"]["words"] == {"count": 30, "sum": 435, "min": 0, "max": 29}
    assert status["samples"] == 25

    seqs = [s["seq"] for s in sent.of("status")]
    assert seqs == sorted(set(seqs))

    samples = sent.last("samples")
    assert [s["n"] for s in samples["latest"]] == list(range(16, 26))
    assert samples["latest"][-1]["fields"]["output"]["id"] == 29
    assert samples["spaced"][0]["n"] == 1
    assert len(samples["spaced"]) <= 20


def test_limit_reads_the_first_items_only(workspace):
    file = workspace.task_file(CLEAN)
    code, sent = workspace.run(file, limit=8)
    assert code == 0
    assert [row["id"] for row in workspace.read_jsonl(workspace.cleaned)] == [5, 6, 7]
    assert sent.last("status")["stages"][0]["total"] == 8
    assert sent[0][1]["limit"] == 8


def test_a_setting_is_replaced(workspace):
    file = workspace.task_file(CLEAN)
    code, _ = workspace.run(file, settings={"min_words": 28})
    assert code == 0
    assert [row["id"] for row in workspace.read_jsonl(workspace.cleaned)] == [28, 29]


def test_a_setting_of_another_kind_is_refused_before_the_run_starts(workspace):
    file = workspace.task_file(CLEAN)
    with pytest.raises(TaskError, match="the setting 'min_words' is a int"):
        workspace.run(file, settings={"min_words": "many"})


def test_an_unknown_setting_is_refused(workspace):
    file = workspace.task_file(CLEAN)
    with pytest.raises(TaskError, match="has no setting speed. Its settings: min_words"):
        workspace.run(file, settings={"speed": 2})


def test_a_missing_input_path_is_named(workspace):
    file = workspace.task_file(CLEAN)
    with pytest.raises(TaskError, match="--in rows=<path>"):
        workspace.run(file, inputs={})


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
    code, sent = workspace.run(file)
    assert code == 1
    status = sent.last("status")
    assert status["state"] == "failed"
    assert status["error"] == "ValueError: row 3 is broken"
    assert status["stages"][0]["done"] == 3
    assert not workspace.cleaned.exists()
    partial = workspace.cleaned.with_name("cleaned.jsonl.partial")
    assert [row["id"] for row in workspace.read_jsonl(partial)] == [0, 1, 2]


def test_a_failed_item_is_sent_and_the_run_goes_on(workspace):
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
    code, sent = workspace.run(file)
    assert code == 0
    status = sent.last("status")
    assert status["state"] == "completed" and status["failures"] == 3
    assert [(f["n"], f["error"], f["fields"]["input"]["id"]) for f in sent.of("failure")] == [
        (1, "no text", 0), (2, "no text", 10), (3, "no text", 20),
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
    code, sent = workspace.run(file)
    assert code == 0
    last = sent.last("samples")["latest"][-1]["fields"]
    assert last["input"]["id"] == 29 and last["output"] == {"id": 29, "kept": True}


def test_a_sample_too_long_is_cut(workspace):
    file = workspace.task_file(
        """
        from thinkube_mill import task, show

        @task(inputs={"rows": "jsonl"}, outputs={"report": "json"})
        def wide(rows):
            show(**{f"field{i}": "x" * 1500 for i in range(40)})
            return {"report": {}}
        """
    )
    code, sent = workspace.run(file, outputs={"report": str(workspace.root / "report.json")})
    assert code == 0
    [sample] = sent.last("samples")["latest"]
    assert list(sample["fields"]) == ["sample"]
    assert "more characters]" in sample["fields"]["sample"]
    assert len(json.dumps(sent.last("samples"))) < 100_000


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
    code, _ = workspace.run(file, outputs={"report": str(report), "note": str(note)})
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
    code, sent = workspace.run(file, outputs={"report": str(workspace.root / "report.json")})
    assert code == 1
    assert "did not set its output 'report'" in sent.last("status")["error"]


def test_one_of_several_tasks_is_named(workspace):
    file = workspace.task_file(
        CLEAN
        + """
    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
    def copy(rows):
        yield from rows
"""
    )
    with pytest.raises(TaskError, match="declares several tasks \\(clean, copy\\)"):
        workspace.run(file)
    code, _ = workspace.run(file, task="copy")
    assert code == 0
    assert len(workspace.read_jsonl(workspace.cleaned)) == 30


def test_a_message_that_cannot_be_sent_ends_the_run_unreported(workspace, capsys):
    from thinkube_mill import _execute
    from thinkube_mill._report import ReportError

    file = workspace.task_file(CLEAN)
    task = _execute.pick_task(_execute.load_tasks(file, None), None, file)
    sent = []

    def send(kind, payload):
        if len(sent) >= 5:
            raise ReportError("NATS does not answer")
        sent.append(kind)

    code, _ = _execute.execute(
        file, task, {"rows": str(workspace.rows)}, {"cleaned": str(workspace.cleaned)}, {}, None, None, "r1", send
    )
    assert code == 4
    assert "its last record could not be sent: NATS does not answer" in capsys.readouterr().err


def test_a_tqdm_bar_of_another_library_is_a_stage(workspace):
    from thinkube_mill import tqdm as mill_tqdm

    mill_tqdm.install()
    file = workspace.task_file(
        """
        from thinkube_mill import task

        @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"})
        def clean(rows):
            from tqdm import tqdm
            for row in tqdm(rows, desc="training", mininterval=0):
                yield row
        """
    )
    code, sent = workspace.run(file)
    assert code == 0
    [stage] = sent.last("status")["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("training", 30, 30)
    assert stage["ended_at"] is not None
