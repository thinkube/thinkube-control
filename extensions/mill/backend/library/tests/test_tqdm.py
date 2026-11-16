# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

from thinkube_mill._cli import main


def test_a_tqdm_bar_of_another_library_is_a_stage(workspace):
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
    code = main(
        [
            "run", str(file), "--in", f"rows={workspace.rows}", "--out", f"cleaned={workspace.cleaned}",
            "--runs-dir", str(workspace.runs), "--run-id", "r1",
        ]
    )
    assert code == 0
    [stage] = workspace.record("r1", "status.json")["stages"]
    assert (stage["name"], stage["total"], stage["done"]) == ("training", 30, 30)
    assert stage["ended_at"] is not None
