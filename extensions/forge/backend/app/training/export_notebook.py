# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0
#
# The cells of the notebook a Forge export executes as a Thinkube Control
# notebook job, with the fine-tuning kernel and no GPU. It converts the merged
# checkpoint of a registered fine-tune to a GGUF file with llama.cpp's own
# converter, at the release the llama-cpp backend runs, and publishes it so
# the LLM Gateway serves it on llama.cpp. Control adds a first cell that sets
# PARAMETERS, the export as app/services/runs.py builds it.
# Each "# %%" line starts a cell.

# %% Export
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import thinkube_models as tkm

P = PARAMETERS
ENTRY = P["catalog_entry"]
QUANT = ENTRY["quantization"]
WORK = Path.home() / "thinkube" / "models" / "forge" / P["run_id"] / "gguf"
WORK.mkdir(parents=True, exist_ok=True)
print(f"Export of {P['source_model']} as {ENTRY['id']} ({QUANT}), llama.cpp {P['llama_cpp_release']}")

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
SOURCE = Path.home() / "thinkube" / "mlflow" / "artifacts" / experiment / latest["run_id"] / "artifacts" / "model"
if not (SOURCE / "config.json").exists():
    raise FileNotFoundError(f"{P['source_model']} is registered, but its checkpoint is not at {SOURCE}")
print(f"Checkpoint: {SOURCE}")

# %% llama.cpp's converter and its gguf package
TOOLS = WORK / f"llama.cpp-{P['llama_cpp_release']}"
if not (TOOLS / "convert_hf_to_gguf.py").exists():
    shutil.rmtree(TOOLS, ignore_errors=True)
    subprocess.run(["git", "clone", "--depth", "1", "--branch", P["llama_cpp_release"],
                    "https://github.com/ggml-org/llama.cpp.git", str(TOOLS)], check=True)
print(f"Converter: {TOOLS / 'convert_hf_to_gguf.py'}")

# %% Convert to GGUF
STAGING = tkm.STAGING_PATH / ENTRY["id"]
shutil.rmtree(STAGING, ignore_errors=True)
STAGING.mkdir(parents=True)
GGUF = STAGING / f"{ENTRY['id'].split('/')[-1]}-{QUANT.lower()}.gguf"
subprocess.run([sys.executable, str(TOOLS / "convert_hf_to_gguf.py"), str(SOURCE), "--outfile", str(GGUF),
                "--outtype", QUANT.lower()],
               env={**os.environ, "PYTHONPATH": str(TOOLS / "gguf-py")}, check=True)
print(f"{GGUF.name}: {GGUF.stat().st_size / 2**30:.2f} GB")

# %% Register the GGUF model for the LLM Gateway
# The file goes to the model folder of an MLflow run of its own and becomes a
# model version; Thinkube Control then writes the catalogue entry, so the
# gateway serves the model on llama.cpp.
from mlflow.exceptions import RestException

client = tkm._mlflow_client()
forge_experiment = client.get_experiment_by_name("forge")
run = client.create_run(forge_experiment.experiment_id, run_name=f"{ENTRY['id']} export",
                        tags={"forge.export_of": P["run_id"], "forge.export": "gguf",
                              "forge.source_model": P["source_model"]})
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
