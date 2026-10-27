# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Publishing a fine-tuned model: its catalogue entry in the user's metadata
repository, <GitHub user>/<GitHub user>-metadata, which thinkube-control
merges over the platform catalogue.

The weights are already in MLflow when this runs: the training merged the
adapter into a 16-bit checkpoint, uploaded it to the model folder of its run
and registered a model version, or an export converted that checkpoint to a
GGUF file or quantized it to NVFP4 the same way. The entry is what makes the LLM Gateway know the
model; vLLM or llama.cpp serves it from that folder.
"""

import base64
import json
from typing import Any, Dict

import httpx

from app.services.metadata_fetcher import _get_github_config

GITHUB_API = "https://api.github.com"

# The fields of a fine-tune's catalogue entry, the same as a platform entry's
CATALOG_FIELDS = (
    "id", "name", "params_b", "active_params_b", "quantization", "context_length",
    "description", "server_type", "task", "reasoning_format", "tool_use",
    "stop_tokens", "license", "gated", "serving_name", "is_finetuned",
)


class FinetunedModelError(Exception):
    """The entry is refused, or GitHub refused to store it; the message says which."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


# How a fine-tune is served: vLLM from the merged 16-bit checkpoint or an
# NVFP4 checkpoint quantized from it (Blackwell GPUs only), or llama.cpp from
# a GGUF file converted from it
SERVING = {"vllm": {"BF16", "NVFP4"}, "llama-cpp": {"Q8_0", "F16"}}


def check_entry(entry: Dict[str, Any]) -> None:
    """Refuse an entry that is incomplete, or that names a serving path other
    than vLLM with the merged 16-bit checkpoint or its NVFP4 quantization, or llama.cpp with a GGUF file."""
    missing = [field for field in CATALOG_FIELDS if field not in entry]
    if missing:
        raise FinetunedModelError(f"catalog_entry is missing: {', '.join(missing)}", 422)
    servers = entry["server_type"]
    if not (isinstance(servers, list) and len(servers) == 1 and servers[0] in SERVING):
        raise FinetunedModelError(f"catalog_entry has server_type {servers}; a fine-tune is served by one of "
                                  f"{[[s] for s in SERVING]}", 422)
    quantizations = SERVING[servers[0]]
    if entry["quantization"] not in quantizations:
        raise FinetunedModelError(f"catalog_entry has quantization {entry['quantization']}; {servers[0]} serves a "
                                  f"fine-tune as {sorted(quantizations)}", 422)
    required = {"task": "text-generation", "is_finetuned": True, "serving_name": entry["id"]}
    wrong = {field: entry[field] for field, value in required.items() if entry[field] != value}
    if wrong:
        raise FinetunedModelError(f"catalog_entry has {wrong}; a fine-tune needs {required}", 422)


def write_entry(entry: Dict[str, Any]) -> str:
    """Put the entry in models.json of the user's metadata repository, in place
    of an entry with the same id. Returns the repository."""
    username, token = _get_github_config()
    if not username or not token:
        raise FinetunedModelError("thinkube-control has no GITHUB_USERNAME and GITHUB_TOKEN to write the catalogue with", 503)
    repo = f"{username}/{username}-metadata"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    url = f"{GITHUB_API}/repos/{repo}/contents/models.json"
    with httpx.Client(headers=headers, timeout=30) as github:
        found = github.get(f"{GITHUB_API}/repos/{repo}")
        if found.status_code == 404:
            raise FinetunedModelError(f"{repo} does not exist; create it as a private repository on GitHub", 409)
        _raise_for(found, f"reading {repo}")
        current = github.get(url)
        if current.status_code == 404:
            catalog, sha = {"models": []}, None
        else:
            _raise_for(current, f"reading {repo}/models.json")
            catalog = json.loads(base64.b64decode(current.json()["content"]))
            sha = current.json()["sha"]
        others = [m for m in catalog.get("models", []) if m["id"] != entry["id"]]
        if others + [entry] == catalog.get("models", []):
            return repo
        body = {
            "message": f"Add {entry['id']} to the model catalogue",
            "content": base64.b64encode(
                (json.dumps({**catalog, "models": others + [entry]}, indent=2) + "\n").encode()
            ).decode(),
        }
        if sha:
            body["sha"] = sha
        _raise_for(github.put(url, json=body), f"writing {repo}/models.json")
    return repo


def _raise_for(response: httpx.Response, doing: str) -> None:
    if response.status_code >= 400:
        raise FinetunedModelError(f"GitHub answered {response.status_code} {doing}: {response.text[:300]}", 502)
