# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0
#
# The cells of the notebook a Forge NVFP4 export executes as a Thinkube
# Control notebook job, on the nvfp4 kernel, with one GPU of a Blackwell node.
# It quantizes the merged checkpoint of a registered fine-tune to NVFP4 with
# NVIDIA's TensorRT Model Optimizer, calibrated on rows of the run's own
# training split, writes a checkpoint vLLM loads (quantization modelopt_fp4),
# and publishes it so the LLM Gateway serves it on vLLM. Control adds a first
# cell that sets PARAMETERS, the export as app/services/runs.py builds it.
# Each "# %%" line starts a cell.

# %% Export
import json
import os
import random
import shutil
from importlib import metadata
from pathlib import Path

import requests
import thinkube_models as tkm
from packaging.requirements import Requirement
from packaging.version import Version

P = PARAMETERS
ENTRY = P["catalog_entry"]
D = P["dataset"]
MLFLOW_MOUNT = Path.home() / "thinkube" / "mlflow"
print(f"NVFP4 export of {P['source_model']} as {ENTRY['id']}, calibrated on {P['calibration_rows']} rows "
      f"of split {D['train']['split']} of {D['name']}")

# The environment the export needs: ModelOpt at the version Forge was built for, and a
# transformers its Hugging Face support declares (the [hf] extra's requirement)
installed = Version(metadata.version("nvidia-modelopt"))
if installed != Version(P["modelopt_version"]):
    raise RuntimeError(f"the nvfp4 environment has nvidia-modelopt {installed}; the export needs "
                       f"nvidia-modelopt[hf]=={P['modelopt_version']}")
needed = next((Requirement(r) for r in metadata.requires("nvidia-modelopt") or []
               if Requirement(r).name == "transformers" and "hf" in str(Requirement(r).marker or "")), None)
if needed is None:
    raise RuntimeError(f"nvidia-modelopt {installed} declares no transformers requirement for its hf extra")
transformers_version = metadata.version("transformers")
if not needed.specifier.contains(transformers_version, prereleases=True):
    raise RuntimeError(f"the nvfp4 environment has transformers {transformers_version}; nvidia-modelopt[hf] "
                       f"{installed} needs transformers{needed.specifier}")
print(f"nvidia-modelopt {installed}, transformers {transformers_version}")

# %% The merged checkpoint, from the MLflow registry
CFG = tkm.get_mlflow_config()


def mlflow_get(path, params):
    r = requests.get(f"{CFG['tracking_uri']}/api/2.0/mlflow/{path}", params=params,
                     headers={"Authorization": f"Bearer {tkm.get_mlflow_token()}"}, timeout=30)
    r.raise_for_status()
    return r.json()


registered = P["source_model"].replace("/", "-")
versions = mlflow_get("model-versions/search", {"filter": f"name='{registered}'"}).get("model_versions", [])
if not versions:
    raise RuntimeError(f"{P['source_model']} is not in the MLflow registry; the run did not register it")
latest = max(versions, key=lambda v: int(v["version"]))
experiment = mlflow_get("runs/get", {"run_id": latest["run_id"]})["run"]["info"]["experiment_id"]
SOURCE = MLFLOW_MOUNT / "artifacts" / experiment / latest["run_id"] / "artifacts" / "model"
if not (SOURCE / "config.json").exists():
    raise FileNotFoundError(f"{P['source_model']} is registered, but its checkpoint is not at {SOURCE}")
print(f"Checkpoint: {SOURCE}")

# %% Calibration: rows of the run's own training split, as the run read them
import pandas as pd
from transformers import AutoModelForCausalLM, AutoTokenizer

SHAREGPT_ROLES = {"system": "system", "human": "user", "user": "user", "gpt": "assistant",
                  "assistant": "assistant", "tool": "tool"}


def example(row):
    """One row as {"messages": [...]} or {"text": ...}; the training read the same."""
    kind, cols = D["mapping"]["kind"], D["mapping"]["columns"]
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


source = MLFLOW_MOUNT / D["train"]["path"]
if not source.exists():
    raise FileNotFoundError(f"split {D['train']['split']} of {D['name']} is not at {source}")
rows = pd.read_parquet(source).to_dict(orient="records")
# The same rows each time the export runs: a sample drawn with a fixed seed
chosen = random.Random(P["seed"]).sample(rows, min(P["calibration_rows"], len(rows)))
tokenizer = AutoTokenizer.from_pretrained(SOURCE)
texts = []
for row in chosen:
    e = example(row)
    texts.append(e["text"] if "text" in e else tokenizer.apply_chat_template(e["messages"], tokenize=False))
print(f"{len(texts)} calibration examples of {len(rows)} rows")

# %% Quantize to NVFP4
import modelopt.torch.quantization as mtq
import torch
from modelopt.torch.export import export_hf_checkpoint

model = AutoModelForCausalLM.from_pretrained(SOURCE, dtype=torch.bfloat16, device_map={"": 0})
model.eval()


def calibrate(m):
    with torch.no_grad():
        for text in texts:
            batch = tokenizer(text, return_tensors="pt", truncation=True, max_length=P["max_length"]).to(m.device)
            m(**batch)


model = mtq.quantize(model, mtq.NVFP4_DEFAULT_CFG, calibrate)
STAGING = tkm.STAGING_PATH / ENTRY["id"]
shutil.rmtree(STAGING, ignore_errors=True)
STAGING.mkdir(parents=True)
export_hf_checkpoint(model, export_dir=str(STAGING))
tokenizer.save_pretrained(str(STAGING))
quant = json.loads((STAGING / "hf_quant_config.json").read_text())["quantization"]
if str(quant.get("quant_algo", "")).upper() != "NVFP4":
    raise RuntimeError(f"ModelOpt wrote quant_algo {quant.get('quant_algo')!r}, not NVFP4")
size = sum(f.stat().st_size for f in STAGING.glob("*.safetensors"))
print(f"NVFP4 checkpoint in {STAGING}: {size / 2**30:.2f} GB, {quant}")

# %% Register the NVFP4 model for the LLM Gateway
# The checkpoint goes to the model folder of an MLflow run of its own and
# becomes a model version; Thinkube Control then writes the catalogue entry,
# so the gateway serves the model on vLLM, on GPUs that compute NVFP4.
from mlflow.exceptions import RestException

client = tkm._mlflow_client()
forge_experiment = client.get_experiment_by_name("forge")
run = client.create_run(forge_experiment.experiment_id, run_name=f"{ENTRY['id']} export",
                        tags={"forge.export_of": P["run_id"], "forge.export": "nvfp4",
                              "forge.source_model": P["source_model"],
                              "forge.calibration_rows": str(len(texts))})
source = tkm._upload_checkpoint(STAGING, run.info.artifact_uri)
client.set_terminated(run.info.run_id)
registered_name = ENTRY["id"].replace("/", "-")
try:
    client.create_registered_model(registered_name)
except RestException as e:
    if e.error_code != "RESOURCE_ALREADY_EXISTS":
        raise
version = client.create_model_version(name=registered_name, source=source, run_id=run.info.run_id)
print(f"MLflow model {registered_name} version {version.version}")

published = requests.post(
    f"https://control.{os.environ['DOMAIN_NAME']}/api/v1/x/control-models/api/models/finetuned",
    json={"catalog_entry": ENTRY},
    headers={"Authorization": f"Bearer {os.environ['THINKUBE_API_TOKEN']}"},
    timeout=120,
)
if published.status_code >= 400:
    raise RuntimeError(f"Thinkube Control did not publish {ENTRY['id']}: {published.status_code} {published.text}")
shutil.rmtree(STAGING)
print(f"Registered {ENTRY['id']}: {published.json()}")
