# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The Meep² job Forge builds, and the notebook job it asks Control for: the
plan or the training, its parameters and the catalogue entry of the model it
registers."""

import pytest

from app.models.forge import Run
from app.services import runs

BASE = {"id": "BSC-LT/salamandraTA-2b-instruct", "name": "SalamandraTA 2B", "params_b": 2.25,
        "active_params_b": None, "context_length": 8192, "reasoning_format": None, "tool_use": False,
        "license": "apache-2.0"}

# tkspark's choices, as Control's /jupyterhub/config lists them
CHOICES = {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16]}

TRAINING = {
    "id": "d1", "name": "ca", "version": 1, "format": "text", "mapping": {"kind": "text", "columns": {"text": "t"}},
    "splits": [{"name": "train", "rows": 2, "bytes": 1, "path": "artifacts/3/r/artifacts/data/train.parquet"},
               {"name": "test", "rows": 1, "bytes": 1, "path": "artifacts/3/r/artifacts/data/test.parquet"}],
}


def a_run(register=True, job=None):
    return Run(id="r1", name="Catalan support", dataset_id="d1", dataset_version=1, base_model=BASE["id"], method="qlora",
               job=job or runs.meepsquared_job("qlora", 1000, None, {}), node="tkspark", register=register,
               model_id="forge/catalan-support" if register else None, status="planning", created_by="u",
               dataset=runs.dataset_for_run(TRAINING, "train", None),
               preprocess={"node": "tkamd2", "job_id": "j0", "key": "k1", "folder": "/home/u/preprocessed/k1",
                           "found": False},
               mlflow_experiment_id="5", mlflow_run_id="m1")


def test_every_cell_of_the_notebook_is_python():
    cells = runs.notebook()["cells"]
    assert [c["source"].splitlines()[0] for c in cells] == [
        "# %% Run", "# %% Dataset: the rows as Meep²'s examples, by the dataset's mapping",
        "# %% Milestones: the evaluation set's rows as Meep² answers them, and the scorer that sends each group's "
        "answers",
        "# %% Job: the model from the cluster's mirror, the data, and the job Forge decided",
        "# %% Preprocess: the data tokenized, checked and cut once, into a folder keyed by what made it",
        "# %% Plan: what Meep² will do on this node, and why",
        "# %% Train: Meep² in its own process, its status sent to Forge while it runs",
        "# %% Register: merge the adapter and publish the model for the LLM Gateway"]
    for cell in cells:
        compile(cell["source"], "cell", "exec")


def test_the_training_runs_meepsquared_with_a_status_file_and_a_stop_file():
    train = runs.notebook()["cells"][6]["source"]
    assert '"--status-file", str(STATUS)' in train and '"--stop-file", str(STOP)' in train
    assert 'RESUME = interrupted_again(lambda: any(OUT.glob("checkpoint-*")))' in train
    assert '["--resume"] if RESUME' in train
    assert "COMPLETED, PAUSED = 0, 75" in train


def test_the_notebook_runs_on_the_meepsquared_kernel():
    assert runs.notebook()["metadata"]["kernelspec"]["name"] == "meepsquared"


def test_a_job_names_steps_or_epochs_and_leaves_the_rest_to_auto_mode():
    assert runs.meepsquared_job("lora", 500, None, {}) == {"task": "sft", "method": "lora", "report_to": "mlflow", "steps": 500}
    assert runs.meepsquared_job("qlora", None, 2, {})["epochs"] == 2
    with pytest.raises(ValueError, match="one of them"):
        runs.meepsquared_job("lora", 500, 1, {})
    with pytest.raises(ValueError, match="one of them"):
        runs.meepsquared_job("lora", None, None, {})
    assert runs.meepsquared_job("fp8", 10, None, {})["method"] == "fp8"
    with pytest.raises(ValueError, match="method"):
        runs.meepsquared_job("fp4", 10, None, {})


def test_a_decision_a_person_sets_goes_to_meepsquareds_recipe_or_overrides():
    job = runs.meepsquared_job("lora", 100, None, {"rank": 32, "learning_rate": 2e-4, "checkpointed_layers": 8,
                                             "compile_layers": False})
    assert job["recipe"] == {"rank": 32, "learning_rate": 2e-4}
    assert job["overrides"] == {"checkpointed_layers": 8, "compile_layers": False}
    assert runs.chosen_decisions(job) == {"rank": 32, "learning_rate": 2e-4, "checkpointed_layers": 8,
                                          "compile_layers": False}


def test_a_decision_meepsquared_does_not_take_is_named():
    with pytest.raises(ValueError, match="generations"):
        runs.meepsquared_job("lora", 100, None, {"generations": 8})
    with pytest.raises(ValueError, match="rank"):
        runs.meepsquared_job("lora", 100, None, {"rank": {"r": 1}})


def test_a_plan_job_asks_for_the_plan_and_registers_nothing():
    request = runs.job_request(a_run(), BASE, "plan", CHOICES)
    p = request["parameters"]
    assert (p["mode"], p["register"], p["catalog_entry"]) == ("plan", False, None)
    assert "resume" not in p
    assert p["preprocessed"] == "/home/u/preprocessed/k1"
    assert (request["node"], request["gpus"], request["kernel_name"]) == ("tkspark", 1, "meepsquared")
    assert request["cell_timeout_seconds"] == runs.PLAN_CELL_SECONDS


# As Control's /jupyterhub/config lists them on this cluster
NODES = {"tkspark": {**CHOICES, "gpus": [0, 1]},
         "tkamd1": {"memory_gb": [2, 4, 8, 16, 32, 48], "cpu_cores": [1, 2, 4, 6, 8, 12, 16], "gpus": [0]},
         "tkamd2": {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16, 24, 32],
                    "gpus": [0, 1, 2]}}


def test_the_data_is_preprocessed_on_the_node_with_the_most_cores_other_than_the_runs():
    assert runs.preprocess_node(NODES, "tkspark") == "tkamd2"
    # tkamd1 and tkspark offer 16 cores alike: tkamd1, which has no GPU
    assert runs.preprocess_node(NODES, "tkamd2") == "tkamd1"
    assert runs.preprocess_node({"tkspark": CHOICES}, "tkspark") == "tkspark"


def test_a_preprocessing_job_runs_on_the_cpu_node_with_its_cores_and_no_gpu():
    run = a_run()
    run.preprocess = None
    request = runs.job_request(run, None, "preprocess", NODES["tkamd2"], node="tkamd2")
    p = request["parameters"]
    assert (p["mode"], p["preprocessed"], p["register"], p["catalog_entry"]) == ("preprocess", None, False, None)
    assert p["dataset"]["train"]["path"] == "artifacts/3/r/artifacts/data/train.parquet"
    # 32 cores of about 1 GiB each and 8 more: 40 GiB, raised to tkamd2's choice of 48
    assert (request["node"], request["gpus"], request["cpu_cores"], request["memory_gb"]) == ("tkamd2", 0, 32, 48)
    assert (request["reserve_memory"], request["cell_timeout_seconds"]) == (False, runs.PREPROCESS_CELL_SECONDS)
    with pytest.raises(ValueError, match="names its node"):
        runs.job_request(run, None, "preprocess", NODES["tkamd2"])
    # A plan or training job reads the preprocessed data, and is refused without it
    with pytest.raises(ValueError, match="has not been preprocessed"):
        runs.job_request(run, BASE, "plan", CHOICES)


def test_a_training_job_carries_its_job_its_files_and_its_catalogue_entry():
    run = a_run()
    run.plan = {"decisions": [], "memory_request_bytes": 38 * 2**30}
    request = runs.job_request(run, BASE, "train", CHOICES)
    p = request["parameters"]
    assert p["job"] == {"task": "sft", "method": "qlora", "report_to": "mlflow", "steps": 1000}
    assert p["stop_file"] == "artifacts/5/m1/artifacts/forge/stop"
    assert (p["mlflow_run_id"], p["mode"]) == ("m1", "train")
    assert p["dataset"]["train"]["path"] == "artifacts/3/r/artifacts/data/train.parquet"
    entry = p["catalog_entry"]
    assert entry["id"] == entry["serving_name"] == "forge/catalan-support"
    assert (entry["server_type"], entry["quantization"], entry["is_finetuned"]) == (["vllm"], "BF16", True)
    # As Meep²'s plan asks
    assert (request["memory_gb"], request["reserve_memory"], request["cpu_cores"]) == (48, True, 8)
    assert request["cell_timeout_seconds"] == runs.TRAIN_CELL_SECONDS


def test_a_run_that_does_not_register_has_no_catalogue_entry():
    run = a_run(register=False)
    run.memory_gb = 32
    assert runs.job_request(run, BASE, "train", CHOICES)["parameters"]["catalog_entry"] is None


def test_a_plan_job_asks_for_the_base_models_weights_and_a_fixed_extra():
    assert runs.job_request(a_run(), BASE, "plan", CHOICES)["memory_gb"] == 16
    big = {**BASE, "params_b": 35}
    # 78 GiB, raised to the node's next memory choice
    assert runs.job_request(a_run(), big, "plan", CHOICES)["memory_gb"] == 96
    with pytest.raises(ValueError, match="params_b"):
        runs.plan_memory_gb({**BASE, "params_b": None})


def test_a_training_job_asks_for_the_memory_meepsquareds_plan_states():
    run = a_run()
    run.plan = {"memory_request_bytes": 40 * 2**30 + 1}
    assert runs.train_memory_gb(run) == 41 and runs.sized(run)
    run.plan = {"memory_request_bytes": None, "decisions": [
        {"name": "memory", "text": "not measured", "reason": "the model trained on the CPU"}]}
    assert not runs.sized(run)
    with pytest.raises(ValueError, match="not measured; the model trained on the CPU"):
        runs.job_request(run, BASE, "train", CHOICES)


def test_a_job_larger_than_the_nodes_largest_choice_is_refused():
    run = a_run()
    run.plan = {"memory_request_bytes": 100 * 2**30}
    with pytest.raises(ValueError, match="needs 100 GiB of memory and tkspark allows at most 96 GiB"):
        runs.job_request(run, BASE, "train", CHOICES)


def test_the_persons_memory_and_cores_are_used_as_given():
    run = a_run()
    run.memory_gb, run.cpu_cores = 64, 12
    request = runs.job_request(run, BASE, "train", CHOICES)
    assert (request["memory_gb"], request["cpu_cores"]) == (64, 12)


def test_a_split_the_dataset_does_not_have_is_named():
    with pytest.raises(ValueError, match="no split validation"):
        runs.dataset_for_run(TRAINING, "validation", None)
    with pytest.raises(ValueError, match="no split validation"):
        runs.dataset_for_run(TRAINING, "train", {"split": "validation"})


def test_the_model_id_comes_from_the_run_name():
    assert runs.model_id("Catalan support, v2!") == "forge/catalan-support-v2"
    with pytest.raises(ValueError):
        runs.model_id("¡¿!")


def test_the_time_left_comes_from_the_status_file_then_from_the_plan():
    run = a_run()
    run.plan = {"estimated_seconds": 7200}
    assert runs.remaining_seconds(run) == 7200
    run.progress = {"state": "paused", "step": 600, "total_steps": 1000, "seconds_per_step": 3.0}
    assert runs.remaining_seconds(run) == 1200
    # Paused after one step of a session: no measured time per step yet, the training's plan states one
    run.progress = {"state": "paused", "step": 601, "total_steps": 1000, "seconds_per_step": None,
                    "plan": {"seconds_per_step": 2.0}}
    assert runs.remaining_seconds(run) == 798
    # Planning: steps are 0 of 0 until the plan is made
    run.progress = {"state": "planning", "step": 0, "total_steps": 0, "seconds_per_step": None}
    assert runs.remaining_seconds(run) == 7200
    run.progress, run.plan = None, {"decisions": []}
    with pytest.raises(ValueError, match="estimated_seconds"):
        runs.remaining_seconds(run)


def test_an_export_converts_the_registered_model_without_a_gpu():
    request = runs.export_request(a_run(), BASE, "Q8_0", "tkamd2")
    for cell in request["notebook"]["cells"]:
        compile(cell["source"], "cell", "exec")
    assert (request["gpus"], request["node"], request["kernel_name"]) == (0, "tkamd2", "fine-tuning")
    entry = request["parameters"]["catalog_entry"]
    assert entry["id"] == entry["serving_name"] == "forge/catalan-support-gguf"
    assert (entry["server_type"], entry["quantization"]) == (["llama-cpp"], "Q8_0")
    assert request["parameters"]["source_model"] == "forge/catalan-support"


def test_an_export_to_another_quantization_is_refused():
    with pytest.raises(ValueError, match="Q8_0"):
        runs.export_request(a_run(), BASE, "Q4_K_M", "tkamd2")


def test_a_run_evaluates_on_a_split_or_a_held_out_share_never_both():
    assert runs.evaluation_of("test", 0) == {"split": "test"}
    assert runs.evaluation_of(None, 0.1) == {"holdout": 0.1}
    assert runs.evaluation_of(None, 0) is None
    with pytest.raises(ValueError, match="not on both"):
        runs.evaluation_of("test", 0.1)
    with pytest.raises(ValueError, match="at most 0.5"):
        runs.evaluation_of(None, 0.6)


def test_the_evaluation_split_is_never_the_training_split():
    with pytest.raises(ValueError, match="must not be the split"):
        runs.dataset_for_run(TRAINING, "train", {"split": "train"})
    dataset = runs.dataset_for_run(TRAINING, "train", {"split": "test"})
    assert dataset["eval"] == {"split": "test", "path": "artifacts/3/r/artifacts/data/test.parquet", "rows": 1}


def test_a_held_out_share_is_meepsquareds_eval_share_and_a_split_is_written_by_the_notebook():
    assert runs.meepsquared_job("lora", 100, None, {}, {"holdout": 0.05})["eval_share"] == 0.05
    by_split = runs.meepsquared_job("lora", 100, None, {"eval_every": 20}, {"split": "test"})
    assert "eval_share" not in by_split and by_split["eval_every"] == 20
    assert runs.chosen_decisions(by_split) == {"eval_every": 20}
    with pytest.raises(ValueError, match="for a run that evaluates"):
        runs.meepsquared_job("lora", 100, None, {"eval_every": 20}, None)
    job_cell = runs.notebook()["cells"][3]["source"]
    assert 'JOB["eval_data"] = str(EVAL)' in job_cell


def test_an_nvfp4_export_calibrates_on_the_runs_own_split_and_is_served_by_vllm():
    run = a_run()
    request = runs.nvfp4_request(run, BASE, "tkspark", 256)
    for cell in request["notebook"]["cells"]:
        compile(cell["source"], "cell", "exec")
    p = request["parameters"]
    assert (request["kernel_name"], request["gpus"], request["node"]) == ("nvfp4", 1, "tkspark")
    assert request["notebook"]["metadata"]["kernelspec"]["name"] == "nvfp4"
    assert (p["calibration_rows"], p["dataset"]["train"]["path"]) == (256, "artifacts/3/r/artifacts/data/train.parquet")
    assert (p["modelopt_version"], p["source_model"]) == ("0.47.0", "forge/catalan-support")
    entry = p["catalog_entry"]
    assert entry["id"] == entry["serving_name"] == "forge/catalan-support-nvfp4"
    assert (entry["server_type"], entry["quantization"], entry["is_finetuned"]) == (["vllm"], "NVFP4", True)
    assert request["memory_gb"] == 16


def test_nvfp4_is_computed_from_compute_capability_10():
    assert runs.nvfp4_capable("12.1") and runs.nvfp4_capable("10.0")
    assert not runs.nvfp4_capable("8.6") and not runs.nvfp4_capable("9.0") and not runs.nvfp4_capable(None)


EVAL_SET = {
    "id": "f1", "name": "flores-dev", "version": 2, "format": "messages",
    "mapping": {"kind": "prompt", "columns": {"prompt": "prompt", "completion": "completion"}},
    "splits": [{"name": "train", "rows": 400, "bytes": 1, "path": "artifacts/3/f/artifacts/data/train.parquet"}],
}
EVAL_COLUMNS = ["prompt", "completion", "direction", "source"]


def test_milestones_name_their_evaluation_set_its_columns_and_metrics():
    found = runs.milestones_of(0.02, EVAL_SET, EVAL_COLUMNS, "train", "direction", "source", ["chrf", "bleu"])
    assert found == {"every": 0.02, "metrics": ["chrf", "bleu"], "eval": {
        "id": "f1", "name": "flores-dev", "version": 2, "mapping": EVAL_SET["mapping"], "split": "train",
        "path": "artifacts/3/f/artifacts/data/train.parquet", "rows": 400, "group_column": "direction",
        "source_column": "source"}}
    assert runs.milestones_of(0.1, None, [], "train", None, None, []) == {"every": 0.1, "eval": None, "metrics": []}
    assert runs.milestones_of(None, None, [], "train", None, None, []) is None


@pytest.mark.parametrize("args,error", [
    ((None, EVAL_SET, EVAL_COLUMNS, "train", None, None, ["chrf"]), "give milestones.every"),
    ((0.1, TRAINING, ["t"], "train", None, None, ["chrf"]), "plain text"),
    ((0.1, EVAL_SET, EVAL_COLUMNS, "dev", None, None, ["chrf"]), "has no split dev"),
    ((0.1, EVAL_SET, EVAL_COLUMNS, "train", "lang", None, ["chrf"]), "group_column: .* has no column lang"),
    ((0.1, EVAL_SET, EVAL_COLUMNS, "train", None, "src", ["chrf"]), "source_column: .* has no column src"),
    ((0.1, EVAL_SET, EVAL_COLUMNS, "train", None, None, []), "milestones.metrics"),
])
def test_milestones_that_cannot_be_scored_are_refused(args, error):
    with pytest.raises(ValueError, match=error):
        runs.milestones_of(*args)


def test_milestones_go_to_meepsquareds_job_and_the_notebook_writes_their_rows_and_scorer():
    milestones = runs.milestones_of(0.02, EVAL_SET, EVAL_COLUMNS, "train", "direction", "source", ["chrf"])
    job = runs.meepsquared_job("lora", None, 1, {}, None, milestones)
    assert job["milestones"] == 0.02 and "milestone_eval" not in job
    run = a_run(job=job)
    run.milestones = milestones
    request = runs.job_request(run, BASE, "plan", CHOICES)
    assert request["parameters"]["milestones"] == milestones
    cell = runs.notebook()["cells"][2]["source"]
    assert "http://scoring.scoring.svc.cluster.local:8080/api/v1/text" in cell
    assert 'MILESTONE_FIELDS = {"milestone_eval": str(MILESTONE_ROWS), "milestone_scorer": f"{SCORER}:score"}' in cell


def test_the_time_left_counts_the_milestones_left():
    run = a_run()
    run.plan = {"decisions": [{"name": "milestones", "value": {"steps": [500, 1000], "generation_seconds": 300}}]}
    run.progress = {"state": "paused", "step": 600, "total_steps": 1000, "seconds_per_step": 3.0}
    assert runs.remaining_seconds(run) == 1200 + 300
    # Once one has been made, its measured seconds count
    run.progress["milestone_seconds"] = 400
    assert runs.remaining_seconds(run) == 1200 + 400


def test_a_run_in_a_slot_stops_before_a_milestone_that_would_end_after_the_slot():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    run = a_run()
    run.plan = {"decisions": [{"name": "milestones", "value": {"steps": [100], "generation_seconds": 600}}]}
    run.progress = {"step": 97, "next_milestone": 100, "seconds_per_step": 10.0}
    # Due in 30 s, it would end 630 s from now: a slot ending in 5 minutes is passed
    assert runs.stops_before_milestone(run, now + timedelta(minutes=5), now, 45)
    # A slot that holds it, or ends before it falls due: no early stop
    assert not runs.stops_before_milestone(run, now + timedelta(minutes=20), now, 45)
    assert not runs.stops_before_milestone(run, now + timedelta(seconds=20), now, 45)
    # Too far ahead to ask yet, and too late: the step before the milestone's has ended
    run.progress = {"step": 50, "next_milestone": 100, "seconds_per_step": 10.0}
    assert not runs.stops_before_milestone(run, now + timedelta(minutes=9), now, 45)
    run.progress = {"step": 99, "next_milestone": 100, "seconds_per_step": 10.0}
    assert not runs.stops_before_milestone(run, now + timedelta(minutes=5), now, 45)



def test_a_job_without_the_persons_cores_gets_the_largest_choice_up_to_eight():
    assert runs.job_cores([1, 2, 4, 6, 8, 12, 16], "tkspark") == 8
    assert runs.job_cores([1, 2, 4, 6], "small") == 6
    with pytest.raises(ValueError, match="no CPU choice of 8 cores or fewer on odd"):
        runs.job_cores([12, 16], "odd")
