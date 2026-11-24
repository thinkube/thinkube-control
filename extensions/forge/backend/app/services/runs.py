# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A fine-tuning run as Meep² training jobs, run as notebook jobs of Thinkube Control.

Forge decides the parts of the Meep² job a person chooses: the method, the
steps or epochs, and any decision of Meep²'s plan the person overrides.
Meep²'s auto mode decides the rest. The notebook built from
app/training/meepsquared_notebook.py adds what only the notebook knows, the paths
of the model mirror, the data and the output, and runs on the meepsquared kernel
with one GPU: once to ask for the plan, then to train."""

import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.forge import Run
from app.services import runfiles

MEEPSQUARED_SCRIPT = Path(__file__).resolve().parents[1] / "training" / "meepsquared_notebook.py"
EXPORT_SCRIPT = Path(__file__).resolve().parents[1] / "training" / "export_notebook.py"
NVFP4_SCRIPT = Path(__file__).resolve().parents[1] / "training" / "nvfp4_notebook.py"
# The NVFP4 export: the Python environment it runs in, the ModelOpt it is built for, and what the
# environment holds (transformers in the range nvidia-modelopt[hf] declares)
NVFP4_KERNEL = "nvfp4"
MODELOPT_VERSION = "0.47.0"
NVFP4_PACKAGES = (f"nvidia-modelopt[hf]=={MODELOPT_VERSION}", "transformers>=4.57,<5.15", "accelerate", "pandas",
                  "pyarrow", "mlflow", "boto3", "kubernetes==36.0.3")
# NVFP4 is computed by Blackwell tensor cores: compute capability 10.0 and higher
NVFP4_MIN_CAPABILITY = (10, 0)
# Calibration: the rows of the run's training split it reads, drawn with NVFP4_SEED, each up to NVFP4_MAX_LENGTH tokens
NVFP4_CALIBRATION_ROWS = 512
NVFP4_SEED = 0
NVFP4_MAX_LENGTH = 2048
# The llama.cpp release the llama-cpp backend runs (LLAMA_CPP_RELEASE in the
# Containerfile of thinkube/tkt-llama-cpp): its converter writes GGUF files that release reads
LLAMA_CPP_RELEASE = "b11476"
GGUF_QUANTIZATIONS = ("Q8_0", "F16")
# The Python environment with Meep²: a Jupyter environment of Thinkube Control, whose kernel has its name
MEEPSQUARED_KERNEL = "meepsquared"
# The kernel the GGUF export runs on
KERNEL = "fine-tuning"
# Control's job states
JOB_OPEN = ("starting", "running")
JOB_FINAL = ("completed", "error", "cancelled", "lost")
UNSUCCESSFUL_JOB = ("error", "cancelled", "lost")
# A run's states in the queue (app/models/forge.py, Run)
BEFORE_PLAN = ("preprocessing", "planning", "revising")
WAITING = ("queued", "paused")
TRAINING = ("running", "stopping")
ENDED = ("completed", "failed", "cancelled")
UNSUCCESSFUL = ("failed", "cancelled")
WHEN = ("now", "slots")
METHODS = ("lora", "qlora", "fp8", "nvfp4")
# A model id the catalogue gives a fine-tune: forge/<name of the run>
MODEL_NAMESPACE = "forge"
# The memory a plan job asks for: the base model's 16-bit weights, which it
# loads, and PLAN_MEMORY_EXTRA_GB for Python, the kernel and the data; at least PLAN_MEMORY_MIN_GB
PLAN_MEMORY_EXTRA_GB = 8
PLAN_MEMORY_MIN_GB = 16
# The memory of a preprocessing job: Meep² preprocesses in passes over chunks of rows, so it does not grow with
# the data, but each core's worker holds about 1 GiB (run 6's 1.9 million rows on 24 cores peaked at 27.2 GiB);
# and the time it may take
PREPROCESS_MEMORY_GB_PER_CORE = 1
PREPROCESS_MEMORY_EXTRA_GB = 8
PREPROCESS_CELL_SECONDS = 4 * 3600
GIB = 2 ** 30
# The seconds a cell of a training may take: the whole training is one cell
TRAIN_CELL_SECONDS = 7 * 24 * 3600
PLAN_CELL_SECONDS = 2 * 3600
# A revision: the plan made from an earlier plan's measurements by Meep², on a node's CPU without loading the model;
# it reads the preprocessed rows and the tokenizer. Its memory and cores, and the time it may take
REVISE_MEMORY_GB = 16
REVISE_CPU_CORES = 2
REVISE_CELL_SECONDS = 1800
# The cores a plan or training job gets unless the person sets them: Meep² tokenizes the data on every core the
# job has, minutes a core per million rows, and a node's default (4 on tkspark) was set for a notebook server
JOB_CPU_CORES = 8

# The fields of Meep²'s Recipe and Overrides a Forge run may set (an SFT job);
# a decision of the plan with one of these names is overridden by that field
RECIPE_FIELDS = ("rank", "alpha", "dropout", "targets", "learning_rate", "schedule", "warmup_steps",
                 "tokens_per_update", "effective_batch", "optimizer", "max_length", "max_grad_norm",
                 "weight_decay", "experts", "answers_only",
                 # a choice job's (Meep²'s task choice)
                 "choice_head", "head_learning_rate", "candidates_per_pass", "head_only", "set_layers",
                 "none_output")
OVERRIDE_FIELDS = ("attention", "liger", "padding_free", "fused_lora", "compile_layers", "checkpointed_layers",
                   "selective_layers", "micro_batch", "milestone_engine")
# Decisions that are fields of the job itself: how many steps apart the evaluations are
JOB_FIELDS = ("eval_every",)
# The largest share of the training split a run may hold out to evaluate on (Meep²'s eval_share)
MAX_HOLDOUT = 0.5


def notebook_cells(script: str) -> List[Dict[str, Any]]:
    """The code cells of a script in the percent format: each "# %%" line
    starts a cell, and what comes before the first one is left out."""
    cells, current = [], None
    for line in script.splitlines():
        if line.startswith("# %%"):
            if current is not None:
                cells.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        cells.append(current)
    return [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": "\n".join(lines).strip() + "\n"} for lines in cells]


def notebook(kernel_name: str = MEEPSQUARED_KERNEL, script: Path = MEEPSQUARED_SCRIPT) -> Dict[str, Any]:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": kernel_name, "display_name": kernel_name, "language": "python"}},
        "cells": notebook_cells(script.read_text()),
    }


def model_id(run_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", run_name.lower()).strip("-")
    if not slug:
        raise ValueError(f"the run name {run_name!r} has no letters or digits to name the model with")
    return f"{MODEL_NAMESPACE}/{slug}"


def catalog_entry(model: str, run: Run, base: Dict[str, Any]) -> Dict[str, Any]:
    """The catalogue entry of the merged model: the base model's facts, served
    by vLLM as a 16-bit checkpoint."""
    return {
        "id": model,
        "name": run.name,
        "params_b": base["params_b"],
        "active_params_b": base.get("active_params_b"),
        "quantization": "BF16",
        "context_length": base["context_length"],
        "description": f"{base['name']} fine-tuned with Thinkube Forge ({run.method.upper()}), run {run.id}.",
        "server_type": ["vllm"],
        "task": "text-generation",
        "reasoning_format": base.get("reasoning_format"),
        "tool_use": base.get("tool_use", False),
        "stop_tokens": [],
        "license": base.get("license"),
        "gated": False,
        "serving_name": model,
        "is_finetuned": True,
    }


def evaluation_of(eval_split: Optional[str], eval_holdout: float) -> Optional[Dict[str, Any]]:
    """How a run evaluates: on a split of its dataset, on a held-out share of its training split, or not at all."""
    if eval_split and eval_holdout:
        raise ValueError("evaluate on a split or on a held-out share, not on both")
    if eval_holdout and not 0 < eval_holdout <= MAX_HOLDOUT:
        raise ValueError(f"a held-out share is above 0 and at most {MAX_HOLDOUT}, not {eval_holdout}")
    if eval_split:
        return {"split": eval_split}
    return {"holdout": eval_holdout} if eval_holdout else None


def dataset_for_run(training: Dict[str, Any], train_split: str, evaluation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What a run reads of a version of a dataset: the version, its format,
    mapping and the files of the splits it trains and evaluates on, below
    ~/thinkube/mlflow."""
    files = {s["name"]: s for s in training["splits"]}
    if train_split not in files:
        raise ValueError(f"dataset {training['name']} has no split {train_split}; it has {sorted(files)}")
    eval_split = (evaluation or {}).get("split")
    if eval_split is not None:
        if eval_split not in files:
            raise ValueError(f"dataset {training['name']} has no split {eval_split}; it has {sorted(files)}")
        if eval_split == train_split:
            raise ValueError("the evaluation split must not be the split the run trains on")
    return {
        "id": training["id"],
        "name": training["name"],
        "version": training["version"],
        "format": training["format"],
        "mapping": training["mapping"],
        "train": {"split": train_split, "path": files[train_split]["path"], "rows": files[train_split]["rows"]},
        "eval": {"split": eval_split, "path": files[eval_split]["path"], "rows": files[eval_split]["rows"]} if eval_split else None,
    }


def milestones_of(every: Optional[float], training: Optional[Dict[str, Any]], columns: List[str], split: str,
                  group_column: Optional[str], source_column: Optional[str], metrics: List[str],
                  task: str = "sft", weights: Optional[str] = None,
                  growth: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """A run's milestones: every `every` of its planned steps; with an evaluation set (`training`, as Datasets'
    /training gives a version, and the version's `columns`), its split, the columns that group its rows and hold
    each row's source, and the metrics the scoring service computes. None without milestones.

    A choice run's evaluation set is a choice set that maps which candidates are right: Meep² counts the rows whose
    top candidate is right, and the scoring service is not asked, so it takes no metrics and no source column.
    weights, a choice run's: the method whose weights answer the rows (Meep²'s milestone_weights), such as qlora for
    the NF4 base a 24 GB GPU serves, when it is not the training's. growth: the milestones at growing distances,
    the first distance `every` of the planned steps and each next one growth times the one before (Meep²'s
    milestone_growth)."""
    if growth is not None and not growth > 1:
        raise ValueError(f"milestones.growth is above 1, the factor from one distance between milestones to the next, "
                         f"not {growth}")
    if weights is not None:
        if weights not in METHODS:
            raise ValueError(f"milestones.weights is one of {list(METHODS)}, not {weights!r}")
        if task != "choice" or training is None:
            raise ValueError("milestones.weights says which weights answer a choice run's evaluation set; give a "
                             "choice evaluation set")
    if every is None:
        if training is not None:
            raise ValueError("an evaluation set is scored at milestones; give milestones.every")
        return None
    if not 0 < every <= 1:
        raise ValueError(f"milestones.every is a share of the planned steps above 0 and at most 1, not {every}")
    spacing = {"growth": growth} if growth is not None else {}
    if training is None:
        return {"every": every, "eval": None, "metrics": [], **spacing}
    kind = training["mapping"]["kind"]
    if (task == "choice") != (kind == "choice"):
        raise ValueError(f"the evaluation set {training['name']} maps its rows as {kind}; a "
                         + ("choice run's milestones need a choice set" if task == "choice"
                            else "choice set is for a run that trains on a choice dataset"))
    if kind == "choice":
        if "right" not in training["mapping"]["columns"]:
            raise ValueError(f"the evaluation set {training['name']} does not map the column right, which says "
                             "which candidates are right")
        if metrics or source_column is not None:
            raise ValueError("a choice run's milestones count the right answers; they take no metrics and no "
                             "source_column")
    if kind == "text":
        raise ValueError(f"the evaluation set {training['name']} maps its rows to plain text; a milestone needs a "
                         "prompt and its expected answer, a mapping of another kind")
    files = {s["name"]: s for s in training["splits"]}
    if split not in files:
        raise ValueError(f"the evaluation set {training['name']} has no split {split}; it has {sorted(files)}")
    for name, column in (("group_column", group_column), ("source_column", source_column)):
        if column is not None and column not in columns:
            raise ValueError(f"{name}: the evaluation set {training['name']} has no column {column}; it has {columns}")
    if kind != "choice" and (not metrics or not all(isinstance(m, str) and m for m in metrics)):
        raise ValueError("milestones.metrics: the scoring service's metrics to compute, such as ['chrf', 'bleu']")
    return {"every": every, "metrics": list(metrics), **spacing,
            **({"weights": weights} if weights is not None else {}),
            "eval": {"id": training["id"], "name": training["name"], "version": training["version"],
                     "mapping": training["mapping"], "split": split, "path": files[split]["path"],
                     "rows": files[split]["rows"], "group_column": group_column, "source_column": source_column}}


def split_decisions(chosen: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The decisions a person overrides, as Meep²'s recipe and overrides.

    Each name is a field of Meep²'s Recipe or Overrides; its value is checked
    by Meep² when the job is planned, and the plan reports it as the person's."""
    known = RECIPE_FIELDS + OVERRIDE_FIELDS + JOB_FIELDS
    unknown = sorted(set(chosen) - set(known))
    if unknown:
        raise ValueError(f"{unknown} are not decisions a run may set; they are {sorted(known)}")
    wrong = sorted(k for k, v in chosen.items() if not isinstance(v, (bool, int, float, str, list)))
    if wrong:
        raise ValueError(f"{wrong}: a decision is a number, true or false, a word, or a list")
    return {"recipe": {k: v for k, v in chosen.items() if k in RECIPE_FIELDS},
            "overrides": {k: v for k, v in chosen.items() if k in OVERRIDE_FIELDS},
            "job": {k: v for k, v in chosen.items() if k in JOB_FIELDS}}


def meepsquared_job(method: str, steps: Optional[int], epochs: Optional[float], chosen: Dict[str, Any],
               evaluation: Optional[Dict[str, Any]] = None,
               milestones: Optional[Dict[str, Any]] = None, task: str = "sft",
               start_from: Optional[str] = None) -> Dict[str, Any]:
    """The Meep² job Forge decides: an SFT job with LoRA or QLoRA, for steps
    or epochs, reporting to MLflow, evaluating on a held-out share when the
    run asks for one; auto mode decides every field not given. A run that
    evaluates on a split gets its eval_data from the notebook, which writes
    the split's examples. task is "choice" for a dataset of the mapping kind
    choice (Meep²'s task choice), "sft" for any other. start_from, a choice
    job's only: a state saved outside Meep² to start from, a .pt file below the
    notebooks' models folder (~/thinkube/models), which the notebook resolves."""
    if method not in METHODS:
        raise ValueError(f"method is one of {list(METHODS)}, not {method!r}")
    if task not in ("sft", "choice"):
        raise ValueError(f"task is 'sft' or 'choice', not {task!r}")
    if (steps is None) == (epochs is None):
        raise ValueError("give the steps or the epochs to train, one of them")
    parts = split_decisions(chosen)
    job: Dict[str, Any] = {"task": task, "method": method, "report_to": "mlflow"}
    if start_from is not None:
        if task != "choice":
            raise ValueError("start_from is a choice run's: a state saved outside Meep² to start from")
        parts_of = Path(start_from).parts
        if Path(start_from).is_absolute() or ".." in parts_of or Path(start_from).suffix != ".pt":
            raise ValueError(f"start_from is a .pt file below the models folder, such as forge/starts/run.pt, "
                             f"not {start_from!r}")
        job["start_from"] = start_from
    if steps is not None:
        job["steps"] = steps
    else:
        job["epochs"] = epochs
    if (evaluation or {}).get("holdout"):
        job["eval_share"] = evaluation["holdout"]
    if parts["job"] and not evaluation:
        raise ValueError("eval_every is for a run that evaluates; choose an evaluation split or a held-out share")
    job.update(parts["job"])
    if milestones is not None:
        # The notebook adds the evaluation's rows and the scorer, which are files on its side
        job["milestones"] = milestones["every"]
        if milestones.get("weights") is not None:
            job["milestone_weights"] = milestones["weights"]
        if milestones.get("growth") is not None:
            job["milestone_growth"] = milestones["growth"]
    for part in ("recipe", "overrides"):
        if parts[part]:
            job[part] = parts[part]
    return job


def chosen_decisions(job: Dict[str, Any]) -> Dict[str, Any]:
    """The decisions a run's job overrides, by name."""
    return {**job.get("recipe", {}), **job.get("overrides", {}), **{k: job[k] for k in JOB_FIELDS if k in job}}


def plan_memory_gb(base: Dict[str, Any]) -> int:
    """The memory of a plan job: the base model's weights in 16 bits, which it loads, and a fixed extra."""
    if base.get("params_b") is None:
        raise ValueError(f"the catalogue states no params_b for {base['id']}, which sizes the memory of its plan job")
    return max(PLAN_MEMORY_MIN_GB, math.ceil(base["params_b"] * 2) + PLAN_MEMORY_EXTRA_GB)


def memory_line(plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The plan's "memory" decision: what Meep² asks for, and why."""
    return next((d for d in (plan or {}).get("decisions", []) if d.get("name") == "memory"), None)


def memory_reason(plan: Optional[Dict[str, Any]]) -> str:
    line = memory_line(plan)
    if line is None:
        return "Meep²'s plan has no memory line"
    return "; ".join(part for part in (line.get("text"), line.get("reason")) if part)


def train_memory_gb(run: Run) -> int:
    """The memory of a training job, as Meep²'s plan states it: memory_request_bytes, its peak and
    Meep²'s own margin, in whole GiB."""
    request = (run.plan or {}).get("memory_request_bytes")
    if request is None:
        raise ValueError(f"run {run.name}: Meep²'s plan cannot size its training job ({memory_reason(run.plan)}); "
                         "set its memory under Advanced")
    return math.ceil(request / GIB)


def allowed_memory(needed_gb: int, choices: List[int], node: str) -> int:
    """The smallest memory Control lets a job on the node ask for that covers what it needs."""
    fitting = [c for c in choices if c >= needed_gb]
    if not fitting:
        raise ValueError(f"the job needs {needed_gb} GiB of memory and {node} allows at most {max(choices)} GiB")
    return min(fitting)


def sized(run: Run) -> bool:
    """Whether the run's training job has a memory: the person's, or the plan's."""
    return run.memory_gb is not None or (run.plan or {}).get("memory_request_bytes") is not None


def preprocess_node(nodes: Dict[str, Dict[str, List[int]]], gpu_node: str) -> str:
    """The node a run's data is preprocessed on: of the GPU nodes other than the run's, the one with the
    largest CPU choice (Control's /jupyterhub/config), of two alike the one of fewer GPUs; the run's node itself
    when no other node has a GPU. The job runs in Meep²'s kernel, a venv that needs a GPU, which Control keeps
    only on the nodes with a GPU, even for a job that uses none."""
    others = [node for node in nodes if node != gpu_node and max(nodes[node].get("gpus") or [0]) > 0]
    if not others:
        return gpu_node
    return max(others, key=lambda node: (max(nodes[node]["cpu_cores"]), -max(nodes[node].get("gpus") or [0]), node))


def job_request(run: Run, base: Optional[Dict[str, Any]], mode: str, choices: Dict[str, List[int]],
                node: Optional[str] = None) -> Dict[str, Any]:
    """The notebook job of a run: the preprocessing of its data ("preprocess", on `node`, with its cores and
    no GPU), its plan from an earlier plan's measurements ("revise", on `node`, with no GPU: Meep² loads no model),
    its plan ("plan") or its training ("train"), the last two on the run's node with one GPU.

    A plan or training job's memory is the person's when they set it, else the base model's for a plan job
    and Meep²'s plan's memory_request_bytes for a training job, raised to the smallest of the node's memory
    choices (Control's /jupyterhub/config) that covers it. Its CPU cores are the person's, else the largest
    of the node's choices up to JOB_CPU_CORES. A plan or training job reads the data the preprocessing
    job made."""
    if mode not in ("preprocess", "revise", "plan", "train"):
        raise ValueError(f"mode is 'preprocess', 'revise', 'plan' or 'train', not {mode!r}")
    if mode in ("preprocess", "revise") and node is None:
        raise ValueError(f"a {mode} job names its node")
    folder = (run.preprocess or {}).get("folder")
    if mode == "preprocess":
        cores = max(choices["cpu_cores"])
        memory = allowed_memory(cores * PREPROCESS_MEMORY_GB_PER_CORE + PREPROCESS_MEMORY_EXTRA_GB,
                                choices["memory_gb"], node)
        folder = None
    elif mode == "revise":
        if not (run.reuse or {}).get("plan"):
            raise ValueError(f"run {run.name}: a revision reuses an earlier plan, and the run names none")
        fitting = [c for c in choices["cpu_cores"] if c >= REVISE_CPU_CORES]
        if not fitting:
            raise ValueError(f"Thinkube Control lists no CPU choice of {REVISE_CPU_CORES} cores or more on {node}: "
                             f"{choices['cpu_cores']}")
        cores = min(fitting)
        memory = allowed_memory(REVISE_MEMORY_GB, choices["memory_gb"], node)
    else:
        node = run.node
        if run.memory_gb is not None:
            memory = run.memory_gb
        else:
            needed = plan_memory_gb(base) if mode == "plan" else train_memory_gb(run)
            memory = allowed_memory(needed, choices["memory_gb"], run.node)
        cores = run.cpu_cores if run.cpu_cores is not None else job_cores(choices["cpu_cores"], run.node)
    if mode != "preprocess":
        if folder is None:
            raise ValueError(f"run {run.name}: its data has not been preprocessed; a {mode} job reads the "
                             "preprocessed data")
    parameters = {
        "run_id": run.id,
        "name": run.name,
        "mode": mode,
        "base_model": run.base_model,
        "dataset": run.dataset,
        "job": run.job,
        "milestones": run.milestones,
        "preprocessed": folder,
        "mlflow_run_id": run.mlflow_run_id,
        "stop_file": runfiles.mount_path(run.mlflow_experiment_id, run.mlflow_run_id, runfiles.STOP),
        "register": run.register and mode == "train",
        "catalog_entry": catalog_entry(run.model_id, run, base) if run.register and mode == "train" else None,
    }
    if mode == "revise":
        parameters["earlier_plan"] = run.reuse["plan"]
        parameters["reused_from"] = reused_from(run.reuse)
    if mode == "plan":
        parameters["measured_because"] = measured_because(run)
    request = {
        "notebook": notebook(),
        "parameters": parameters,
        "kernel_name": MEEPSQUARED_KERNEL,
        "node": node,
        "gpus": 1 if mode in ("plan", "train") else 0,
        "memory_gb": memory,
        # A training must not be stopped for memory halfway through
        "reserve_memory": mode in ("plan", "train"),
        # The training is one cell, as long as the training itself
        "cell_timeout_seconds": {"train": TRAIN_CELL_SECONDS, "plan": PLAN_CELL_SECONDS,
                                 "preprocess": PREPROCESS_CELL_SECONDS, "revise": REVISE_CELL_SECONDS}[mode],
        "cpu_cores": cores,
    }
    return request


def reuse_of(run: Run, plan: Optional[Dict[str, Any]], plan_job_id: Optional[str], run_name: str,
             node: str) -> Optional[Dict[str, Any]]:
    """An earlier plan for the run to reuse the measurements of, as Forge keeps it in run.reuse: {"plan", "job_id",
    "run", "node"}; None when the plan keeps no measurements (it measured nothing, or Meep² made it before plans kept
    them). Meep² decides whether they hold for the run's job."""
    if not plan or not plan.get("measurements"):
        return None
    return {"plan": plan, "job_id": plan_job_id, "run": run_name, "node": node}


def other_plan(run: Run, others: List[Run]) -> Optional[Dict[str, Any]]:
    """The newest plan among other runs' that keeps measurements and was made for the same model, method and task on
    the run's node, whose GPU the measurements were made on; for the run's first plan."""
    task = (run.job or {}).get("task", "sft")
    same = [o for o in others if o.id != run.id and o.kind == "finetune" and o.base_model == run.base_model
            and o.method == run.method and o.node == run.node and (o.job or {}).get("task", "sft") == task
            and (o.plan or {}).get("measurements")]
    if not same:
        return None
    newest = max(same, key=lambda o: o.created_at)
    return reuse_of(run, newest.plan, newest.plan_job_id, newest.name, newest.node)


def reused_from(reuse: Dict[str, Any]) -> str:
    """How a plan names the earlier plan whose measurements it reused."""
    return f"Forge's plan job {reuse['job_id']} of run {reuse['run']}"


def measured_because(run: Run) -> str:
    """Why the run's plan job measures, for the plan's line "measurements"."""
    if run.measure:
        return "asked to measure again"
    if (run.reuse or {}).get("measure_again"):
        return f"the measurements of {reused_from(run.reuse)} do not hold: {run.reuse['measure_again']}"
    return "no earlier plan of this model, method and task on this node keeps measurements"


def job_cores(cpu_choices: List[int], node: str) -> int:
    """The largest of the node's CPU choices up to JOB_CPU_CORES."""
    fitting = [c for c in cpu_choices if c <= JOB_CPU_CORES]
    if not fitting:
        raise ValueError(f"Thinkube Control lists no CPU choice of {JOB_CPU_CORES} cores or fewer on {node}: "
                         f"{cpu_choices}")
    return max(fitting)


def gguf_entry(run: Run, base: Dict[str, Any], quantization: str) -> Dict[str, Any]:
    """The catalogue entry of the run's model as a GGUF file, served by llama.cpp."""
    model = f"{run.model_id}-gguf"
    return {
        **catalog_entry(model, run, base),
        "name": f"{run.name} (GGUF {quantization})",
        "quantization": quantization,
        "server_type": ["llama-cpp"],
    }


def export_request(run: Run, base: Dict[str, Any], quantization: str, node: str) -> Dict[str, Any]:
    """The notebook job that converts the run's registered model to a GGUF file and publishes it."""
    if quantization not in GGUF_QUANTIZATIONS:
        raise ValueError(f"a GGUF export is one of {list(GGUF_QUANTIZATIONS)}")
    return {
        "notebook": notebook(KERNEL, EXPORT_SCRIPT),
        "parameters": {
            "run_id": run.id,
            "source_model": run.model_id,
            "llama_cpp_release": LLAMA_CPP_RELEASE,
            "catalog_entry": gguf_entry(run, base, quantization),
        },
        "kernel_name": KERNEL,
        "node": node,
        # The conversion reads and writes files; it needs no GPU
        "gpus": 0,
        "memory_gb": 16,
        "cpu_cores": 4,
        "cell_timeout_seconds": 6 * 3600,
    }


def nvfp4_capable(compute_capability: Optional[str]) -> bool:
    """Whether a GPU of this compute capability ("major.minor") computes NVFP4."""
    if not compute_capability:
        return False
    major, minor = (int(part) for part in compute_capability.split(".", 1))
    return (major, minor) >= NVFP4_MIN_CAPABILITY


def nvfp4_entry(run: Run, base: Dict[str, Any]) -> Dict[str, Any]:
    """The catalogue entry of the run's model in NVFP4, served by vLLM on GPUs that compute it."""
    model = f"{run.model_id}-nvfp4"
    return {**catalog_entry(model, run, base), "name": f"{run.name} (NVFP4)", "quantization": "NVFP4"}


def nvfp4_request(run: Run, base: Dict[str, Any], node: str, calibration_rows: int) -> Dict[str, Any]:
    """The notebook job that quantizes the run's registered model to NVFP4, calibrated on rows of the
    split it trained on, and publishes it: one GPU of the node, the memory of the base model's 16-bit
    weights and a fixed extra, as a plan job asks."""
    return {
        "notebook": notebook(NVFP4_KERNEL, NVFP4_SCRIPT),
        "parameters": {
            "run_id": run.id,
            "source_model": run.model_id,
            "dataset": run.dataset,
            "calibration_rows": calibration_rows,
            "seed": NVFP4_SEED,
            "max_length": NVFP4_MAX_LENGTH,
            "modelopt_version": MODELOPT_VERSION,
            "catalog_entry": nvfp4_entry(run, base),
        },
        "kernel_name": NVFP4_KERNEL,
        "node": node,
        "gpus": 1,
        "memory_gb": run.memory_gb if run.memory_gb is not None else plan_memory_gb(base),
        "reserve_memory": True,
        "cell_timeout_seconds": 6 * 3600,
    }


def milestone_seconds(run: Run) -> float:
    """The seconds of one milestone of the run: the latest one's, as its status file measured, else the
    generation Meep²'s plan timed; 0 for milestones without an evaluation before one has been made, as the plan
    times only the generation."""
    p = run.progress or {}
    if p.get("milestone_seconds") is not None:
        return float(p["milestone_seconds"])
    line = next((d for d in (run.plan or {}).get("decisions", []) if d.get("name") == "milestones"), None)
    return float(((line or {}).get("value") or {}).get("generation_seconds") or 0)


def milestones_left(run: Run) -> int:
    """The milestones of the run's plan after the step its status file reached."""
    line = next((d for d in (run.plan or {}).get("decisions", []) if d.get("name") == "milestones"), None)
    at = ((line or {}).get("value") or {}).get("steps") or []
    step = (run.progress or {}).get("step") or 0
    return sum(1 for s in at if s > step)


def stops_before_milestone(run: Run, slot_end: datetime, now: datetime, lead_seconds: float) -> bool:
    """Whether a run training in a slot is asked to stop now, before its next milestone: the milestone falls due
    before the slot's end and would end after it. Meep² runs a milestone before a stop at its step takes effect,
    so the stop must be in place when the step before the milestone's ends: it is asked for when that step ends
    within `lead_seconds`, while the status file's step is before it."""
    p = run.progress or {}
    upcoming, step, per_step = p.get("next_milestone"), p.get("step"), p.get("seconds_per_step")
    if upcoming is None or step is None or per_step is None or step > upcoming - 2:
        return False
    if (upcoming - 1 - step) * per_step > lead_seconds:
        return False
    due = now + timedelta(seconds=(upcoming - step) * per_step)
    return due <= slot_end < due + timedelta(seconds=milestone_seconds(run))


def remaining_seconds(run: Run) -> float:
    """How long the run still trains. A run that has trained: its steps left,
    at the seconds per step its status file measured, or, before the second
    step of a session measures one, at the seconds per step its training's
    plan states. A run that has not: its plan's estimate. A run without
    either cannot be fitted into a slot."""
    p = run.progress or {}
    if p.get("total_steps") and p.get("step") is not None:
        per_step = p.get("seconds_per_step")
        if per_step is None:
            per_step = (p.get("plan") or {}).get("seconds_per_step")
        if per_step is not None:
            return (float(per_step) * max(0, int(p["total_steps"]) - int(p["step"]))
                    + milestones_left(run) * milestone_seconds(run))
    estimate = (run.plan or {}).get("estimated_seconds")
    if estimate is None:
        raise ValueError(f"run {run.name}: Meep²'s plan states no estimated_seconds, which a run that waits "
                         "for a slot needs to be fitted into one; queue it 'now', or across slots")
    return float(estimate)


def eta_seconds(run: Run) -> Optional[float]:
    """The seconds until the run ends, for the pages: the status file's, else the plan's estimate."""
    p = run.progress or {}
    if p.get("eta_seconds") is not None:
        return float(p["eta_seconds"])
    if run.status in ("planned", "queued") + BEFORE_PLAN:
        estimate = (run.plan or {}).get("estimated_seconds")
        return float(estimate) if estimate is not None else None
    return None
