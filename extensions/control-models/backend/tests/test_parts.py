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
