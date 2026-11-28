# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A trained part's files are checked, written into a run's artifact folder on MLflow's file system with an
MLmodel naming the flavor meepserve, and registered as a model version under the part's id."""

import json

import pytest
import yaml

from app.services import parts
from app.services.finetuned_models import FinetunedModelError

ENTRY = {"id": "kubexlat/ja-parse", "base_model": "FacebookAI/xlm-roberta-large"}
CONTRACT = {"kind": "tagger", "format": 1, "base": "FacebookAI/xlm-roberta-large"}


def test_a_parts_files_are_plain_names_with_its_contract_naming_the_entrys_base():
    parts.check_files(["meepserve.json", "tagger.json", "weights.pt"], CONTRACT, ENTRY)
    for names, contract, message in [
        (["tagger.json"], CONTRACT, "include its contract"),
        (["meepserve.json", "sub/weights.pt"], CONTRACT, "without folders"),
        (["meepserve.json", "../weights.pt"], CONTRACT, "without folders"),
        (["meepserve.json", "MLmodel"], CONTRACT, "do not include MLmodel"),
        (["meepserve.json"], {**CONTRACT, "base": "other/base"}, "names the base other/base"),
        (["meepserve.json"], {"kind": "tagger", "base": "FacebookAI/xlm-roberta-large"}, "'format' is required"),
    ]:
        with pytest.raises(FinetunedModelError, match=message):
            parts.check_files(names, contract, ENTRY)


def test_a_runs_artifacts_are_under_artifacts_on_mlflows_file_system(tmp_path):
    assert parts.artifact_folder("mlflow-artifacts:/7/abc/artifacts", tmp_path) == tmp_path / "artifacts/7/abc/artifacts"
    with pytest.raises(FinetunedModelError, match="not under mlflow-artifacts:/"):
        parts.artifact_folder("s3://mlflow/7/abc/artifacts", tmp_path)


class FakeMlflow:
    def __init__(self):
        self.calls = []

    def experiment(self, name):
        self.calls.append(("experiment", name))
        return "7"

    def run(self, experiment_id, name):
        self.calls.append(("run", experiment_id, name))
        return "abc", "mlflow-artifacts:/7/abc/artifacts"

    def finish(self, run_id):
        self.calls.append(("finish", run_id))

    def version(self, name, run_id):
        self.calls.append(("version", name, run_id))
        return "1"


def test_a_part_is_written_into_its_run_and_registered_under_its_id(tmp_path):
    mlflow = FakeMlflow()
    files = {"meepserve.json": json.dumps(CONTRACT).encode(), "tagger.json": b"{}", "weights.pt": b"\x00\x01"}
    written = parts.write_part(mlflow, ENTRY, files, tmp_path)
    folder = tmp_path / "artifacts/7/abc/artifacts/model"
    assert written == {"run_id": "abc", "version": "1", "folder": str(folder)}
    assert (folder / "weights.pt").read_bytes() == b"\x00\x01"
    flavor = yaml.safe_load((folder / "MLmodel").read_text())["flavors"]["meepserve"]
    assert flavor == {"model_files": ["meepserve.json", "tagger.json", "weights.pt"], "kind": "tagger", "format": 1,
                      "base": "FacebookAI/xlm-roberta-large"}
    assert mlflow.calls == [("experiment", "model-registry"), ("run", "7", "part-kubexlat-ja-parse"), ("finish", "abc"),
                            ("version", "kubexlat-ja-parse", "abc")]


def _safetensors(path, tensors):
    """A safetensors file of `tensors` {name: shape}, its header only followed by zeros (bfloat16)."""
    import json
    import math
    import struct

    offset, header = 0, {}
    for name, shape in tensors.items():
        size = 2 * math.prod(shape)
        header[name] = {"dtype": "BF16", "shape": shape, "data_offsets": [offset, offset + size]}
        offset += size
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + bytes(offset))


def test_the_base_is_sized_as_the_heads_backend_holds_it(tmp_path):
    from app.services import parts

    _safetensors(tmp_path / "model-1.safetensors", {
        "model.language_model.embed_tokens.weight": [1000, 64],
        "model.language_model.layers.0.self_attn.q_proj.weight": [64, 64],
        "model.language_model.layers.0.input_layernorm.weight": [64]})
    _safetensors(tmp_path / "model-2.safetensors", {"model.language_model.layers.0.mlp.down_proj.weight": [64, 128]})
    linear = 64 * 64 + 64 * 128
    assert parts.held_bytes(tmp_path, "bf16") == 2 * (1000 * 64 + 64 + linear)
    assert parts.held_bytes(tmp_path, "nf4") == 2 * (1000 * 64 + 64) + linear // 2 + 4 * (linear // 64)
    with pytest.raises(parts.FinetunedModelError, match="'weights'"):
        parts.held_bytes(tmp_path, "fp8")
    with pytest.raises(parts.FinetunedModelError, match="no .safetensors"):
        parts.held_bytes(tmp_path / "missing", "nf4")
