# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0
#
# The cells of the notebook a Forge run executes as a Thinkube Control
# notebook job, on the meepsquared kernel. Control adds a first cell that sets
# PARAMETERS, the run as app/services/runs.py builds it. Each "# %%" line
# starts a cell.
#
# PARAMETERS["mode"] is "preprocess", "plan" or "train". A preprocessing job
# writes the data as Meep²'s examples and preprocesses it (tokenized, checked,
# cut) into a folder keyed by what made it, on a CPU node; a plan job reads
# that folder, writes Meep²'s plan and stops; a training job reads it and
# runs `python -m meepsquared train` with a status file and a stop file, and
# registers the model when the training completes.

# %% Run
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import thinkube_models as tkm
import yaml

P = PARAMETERS
RUN = P["run_id"]
D = P["dataset"]
MODE = P["mode"]
if MODE not in ("preprocess", "plan", "train"):
    raise ValueError(f"mode is 'preprocess', 'plan' or 'train', not {MODE!r}")
# The run's own folder on the shared models volume: the data, the job, the
# status file and Meep²'s output (checkpoints and the adapter)
RUN_DIR = Path.home() / "thinkube" / "models" / "forge" / RUN
RUN_DIR.mkdir(parents=True, exist_ok=True)
OUT = RUN_DIR / "output"
STATUS = RUN_DIR / "status.json"
# The MLflow artifacts, which the notebook server mounts here
MLFLOW_MOUNT = Path.home() / "thinkube" / "mlflow"
# Forge puts the stop file among the MLflow run's artifacts; Meep² watches it on the mount
STOP = MLFLOW_MOUNT / P["stop_file"]
print(f"Forge run {RUN} ({P['name']}), {MODE}: dataset {D['name']}, split {D['train']['split']} "
      f"({D['train']['rows']} rows), base {P['base_model']}")


def send(path, name):
    """A file into the forge/ folder of the run's MLflow run, through MLflow's artifact proxy."""
    tkm._mlflow_client().log_artifact(P["mlflow_run_id"], str(path), "forge")


def interrupted_again(call, attempts=5):
    """call(), made again when it raises InterruptedError, up to attempts times. The run's folder is on
    JuiceFS, a FUSE filesystem, which answers EINTR when a request is interrupted; Python retries an
    open or a read on EINTR, but not a stat."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except InterruptedError:
            if attempt == attempts:
                raise
            time.sleep(1)


# %% Dataset: the rows as Meep²'s examples, by the dataset's mapping
import pandas as pd
import pyarrow.parquet as pq

SHAREGPT_ROLES = {"system": "system", "human": "user", "user": "user", "gpt": "assistant",
                  "assistant": "assistant", "tool": "tool"}


def example(row, mapping):
    """One row as {"messages": [...]} or {"text": ...}; the Datasets extension maps the same way."""
    kind, cols = mapping["kind"], mapping["columns"]
    if kind == "text":
        return {"text": row[cols["text"]]}
    if kind == "messages":
        return {"messages": [{"role": t["role"], "content": t["content"]} for t in row[cols["messages"]]]}
    if kind == "sharegpt":
        return {"messages": [{"role": SHAREGPT_ROLES[t["from"]], "content": t["value"]} for t in row[cols["conversations"]]]}
    messages = []
    if kind == "prompt":
        if cols.get("system") and row.get(cols["system"]):
            messages.append({"role": "system", "content": row[cols["system"]]})
        messages += [{"role": "user", "content": row[cols["prompt"]]},
                     {"role": "assistant", "content": row[cols["completion"]]}]
    else:
        prompt = row[cols["instruction"]]
        if cols.get("input") and row.get(cols["input"]):
            prompt = f"{prompt}\n\n{row[cols['input']]}"
        messages += [{"role": "user", "content": prompt}, {"role": "assistant", "content": row[cols["output"]]}]
    return {"messages": messages}


def write(part, name):
    """A split's rows as examples in a JSON Lines file of the run's folder, read
    by Arrow batches so the memory holds one batch; the same rows in the same
    order every time. The preprocessing job writes it; a plan or training job
    reads what it wrote, so a resumed training reads the data it started on."""
    target = RUN_DIR / name
    if MODE != "preprocess":
        if not target.exists():
            raise FileNotFoundError(f"{target} is not there: the run's data was not preprocessed")
        return target
    source = MLFLOW_MOUNT / part["path"]
    if not source.exists():
        raise FileNotFoundError(f"split {part['split']} of {D['name']} is not at {source}")
    with target.open("w") as f:
        for batch in pq.ParquetFile(source).iter_batches(batch_size=10_000):
            for row in batch.to_pylist():
                f.write(json.dumps(example(row, D["mapping"]), default=str) + "\n")
    print(f"{part['rows']} examples of split {part['split']} in {target}")
    return target


DATA = write(D["train"], "data.jsonl")
# The split the run evaluates on, when it is a split of the dataset; a held-out share is the job's eval_share
EVAL = write(D["eval"], "eval.jsonl") if D.get("eval") else None

# %% Milestones: the evaluation set's rows as Meep² answers them, and the scorer that sends each group's answers
# to the scoring service
M = P["milestones"]
MILESTONE_FIELDS = {}
SCORER_CODE = '''
import json, os, time, urllib.error, urllib.request

URL = "http://scoring.scoring.svc.cluster.local:8080/api/v1/text"
METRICS = __METRICS__
# The scoring service answers 503 while it is not ready; it is asked again up to ATTEMPTS times, PAUSE seconds apart
ATTEMPTS, PAUSE = 6, 60


def score(hypotheses, references):
    body = json.dumps({"hypotheses": hypotheses, "references": [[r] for r in references],
                       "metrics": METRICS}).encode()
    request = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ["THINKUBE_API_TOKEN"]})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as answer:
                return {name: found["score"] for name, found in json.load(answer)["metrics"].items()}
        except urllib.error.HTTPError as e:
            if e.code != 503 or attempt == ATTEMPTS:
                raise RuntimeError(f"the scoring service answered {e.code}: {e.read().decode(errors='replace')[:300]}")
            time.sleep(PAUSE)
'''
if M is not None and M["eval"] is not None:
    E = M["eval"]
    source = MLFLOW_MOUNT / E["path"]
    if not source.exists():
        raise FileNotFoundError(f"split {E['split']} of the evaluation set {E['name']} is not at {source}")
    MILESTONE_ROWS = RUN_DIR / "milestone-eval.jsonl"
    with MILESTONE_ROWS.open("w") as f:
        for i, row in enumerate(pd.read_parquet(source).to_dict(orient="records")):
            messages = example(row, E["mapping"])["messages"]
            if messages[-1]["role"] != "assistant":
                raise ValueError(f"row {i} of the evaluation set {E['name']} has no answer to score against")
            item = {"messages": messages[:-1], "reference": messages[-1]["content"]}
            if E["group_column"]:
                item["group"] = str(row[E["group_column"]])
            if E["source_column"]:
                item["source"] = str(row[E["source_column"]])
            f.write(json.dumps(item, default=str) + "\n")
    SCORER = RUN_DIR / "milestone_scorer.py"
    SCORER.write_text(SCORER_CODE.replace("__METRICS__", json.dumps(M["metrics"])))
    MILESTONE_FIELDS = {"milestone_eval": str(MILESTONE_ROWS), "milestone_scorer": f"{SCORER}:score"}
    print(f"{E['rows']} rows of the evaluation set {E['name']} v{E['version']} answered at each milestone, "
          f"scored with {', '.join(M['metrics'])}")

# %% Job: the model from the cluster's mirror, the data, and the job Forge decided
JOB = {**P["job"], "model": tkm.mirrored_model_path(P["base_model"]), "data": str(DATA), "output": str(OUT),
       **MILESTONE_FIELDS}
if EVAL is not None:
    JOB["eval_data"] = str(EVAL)
# Where the preprocessed data is kept, under its key; a plan or training job reads the folder its
# preprocessing job made
PREPROCESSED_DIR = Path.home() / "thinkube" / "models" / "forge" / "preprocessed"
if MODE != "preprocess":
    if not P.get("preprocessed"):
        raise ValueError("a plan or training job names the folder of its preprocessed data")
    JOB["preprocessed"] = P["preprocessed"]
JOB_FILE = RUN_DIR / "job.yaml"
JOB_FILE.write_text(yaml.safe_dump(JOB, sort_keys=False))
send(JOB_FILE, "job.yaml")
print(JOB_FILE.read_text())

# %% Preprocess: the data tokenized, checked and cut once, into a folder keyed by what made it
if MODE == "preprocess":
    from meepsquared.job import TrainingJob

    outcome = TrainingJob.from_dict(JOB).preprocess(PREPROCESSED_DIR)
    print(f"{'found' if outcome.found else 'built'}: {outcome.folder}")
    print(outcome.report["decision"]["text"])
    PREPROCESS_FILE = RUN_DIR / "preprocess.json"
    PREPROCESS_FILE.write_text(json.dumps(outcome.to_dict(), indent=2, default=str))
    send(PREPROCESS_FILE, "preprocess.json")

# %% Plan: what Meep² will do on this node, and why
if MODE == "plan":
    from meepsquared.job import TrainingJob

    plan = TrainingJob.from_dict(JOB).plan()
    print(plan)
    PLAN_FILE = RUN_DIR / "plan.json"
    PLAN_FILE.write_text(json.dumps(plan.to_dict(), indent=2, default=str))
    send(PLAN_FILE, "plan.json")

# %% Train: Meep² in its own process, its status sent to Forge while it runs
# MLflow reads MLFLOW_TRACKING_TOKEN at each request and a Keycloak token
# lasts minutes, so the training's process renews its own.
LAUNCH = RUN_DIR / "launch.py"
LAUNCH.write_text('''
import os, runpy, sys, threading
import thinkube_models as tkm

os.environ["MLFLOW_TRACKING_TOKEN"] = tkm.get_mlflow_token()


def renew():
    while not threading.Event().wait(120):
        os.environ["MLFLOW_TRACKING_TOKEN"] = tkm.get_mlflow_token()


threading.Thread(target=renew, daemon=True).start()
sys.argv = ["meepsquared", *sys.argv[1:]]
runpy.run_module("meepsquared", run_name="__main__", alter_sys=True)
''')
# Meep²'s exit codes: completed, and paused at a stop file
COMPLETED, PAUSED = 0, 75
FINAL = None
if MODE == "train":
    # The output folder is this run's alone: a checkpoint in it is this run's, which the training continues
    RESUME = interrupted_again(lambda: any(OUT.glob("checkpoint-*")))
    command = [sys.executable, str(LAUNCH), "train", str(JOB_FILE), "--status-file", str(STATUS),
               "--stop-file", str(STOP)] + (["--resume"] if RESUME else [])
    env = {**os.environ, "MLFLOW_EXPERIMENT_NAME": "forge", "MLFLOW_RUN_ID": P["mlflow_run_id"]}
    print(" ".join(command), flush=True)
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def echo():
        for line in process.stdout:
            print(line, end="", flush=True)

    reader = threading.Thread(target=echo, daemon=True)
    reader.start()
    sent = None
    while True:
        ended = process.poll() is not None
        changed = interrupted_again(lambda: STATUS.stat().st_mtime if STATUS.exists() else sent)
        if changed != sent:
            sent = changed
            interrupted_again(lambda: send(STATUS, "status.json"))
        if ended:
            break
        time.sleep(10)
    reader.join()
    code = process.returncode
    status = interrupted_again(lambda: json.loads(STATUS.read_text()) if STATUS.exists() else {})
    if code not in (COMPLETED, PAUSED):
        raise RuntimeError(f"Meep² exited with {code}: {status.get('error') or 'see its output above'}")
    FINAL = status["state"]
    print(f"Meep² exited with {code}: {FINAL}, step {status.get('step')} of {status.get('total_steps')}")

# %% Register: merge the adapter and publish the model for the LLM Gateway
# The merged 16-bit checkpoint becomes a model version in MLflow; Thinkube
# Control then writes the catalogue entry and records the weights, so the
# gateway lists the model.
if MODE == "train" and FINAL == "completed" and P["register"]:
    import gc

    import requests
    import torch
    from mlflow.exceptions import RestException
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    gc.collect()
    torch.cuda.empty_cache()
    BASE = tkm.mirrored_model_path(P["base_model"])
    tokenizer = AutoTokenizer.from_pretrained(OUT)
    base = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16, device_map={"": 0})
    merged = PeftModel.from_pretrained(base, str(OUT)).merge_and_unload()

    class Merged:
        """What the staging step saves: the merged 16-bit checkpoint."""

        def save_pretrained_merged(self, path, tok, save_method):
            merged.save_pretrained(path, safe_serialization=True)
            tok.save_pretrained(path)

    entry = P["catalog_entry"]
    client = tkm._mlflow_client()
    staging = tkm._merge_to_staging(Merged(), tokenizer, entry["id"], P["mlflow_run_id"])
    source = tkm._upload_checkpoint(staging, client.get_run(P["mlflow_run_id"]).info.artifact_uri)
    client = tkm._mlflow_client()
    registered_name = entry["id"].replace("/", "-")
    try:
        client.create_registered_model(registered_name)
    except RestException as e:
        if e.error_code != "RESOURCE_ALREADY_EXISTS":
            raise
    version = client.create_model_version(name=registered_name, source=source, run_id=P["mlflow_run_id"])
    print(f"MLflow model {registered_name} version {version.version}")

    published = requests.post(
        f"https://control.{os.environ['DOMAIN_NAME']}/api/v1/x/control-models/api/models/finetuned",
        json={"catalog_entry": entry},
        headers={"Authorization": f"Bearer {os.environ['THINKUBE_API_TOKEN']}"},
        timeout=120,
    )
    if published.status_code >= 400:
        raise RuntimeError(f"Thinkube Control did not publish {entry['id']}: {published.status_code} {published.text}")
    print(f"Registered {entry['id']}: {published.json()}")
