#!/usr/bin/env python3
"""Only the parameters a template declares reach an application's environment."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from manifest_parameters import (  # noqa: E402
    PARAMETERS_KEY,
    declared_parameter_names,
    deployed_values,
    encode,
    recorded_values,
)

DEPLOY_PARAMS = {
    "model_id": "Qwen/Qwen3.5-4B",
    "ANSIBLE_BECOME_PASS": "admin-password",
    "ANSIBLE_SSH_PRIVATE_KEY_FILE": "/home/thinkube/.ssh/id",
    "MASTER_NODE_IP": "192.168.1.10",
    "AUTHOR_NAME": "someone",
}


def test_the_installer_variables_are_not_parameters():
    names = declared_parameter_names({"parameters": [{"name": "model_id"}]})
    assert deployed_values(names, DEPLOY_PARAMS) == {"model_id": "Qwen/Qwen3.5-4B"}


def test_a_template_that_declares_none_passes_none():
    assert deployed_values(declared_parameter_names({"parameters": []}), DEPLOY_PARAMS) == {}


def test_a_manifest_without_the_list_is_refused():
    with pytest.raises(ValueError, match="parameters: \\[\\]"):
        declared_parameter_names({"name": "app"})


def test_regeneration_reads_back_what_the_deploy_recorded():
    values = deployed_values(["model_id"], DEPLOY_PARAMS)
    data = {"config": "spec: {}", PARAMETERS_KEY: encode(values)}
    assert recorded_values(["model_id"], data, "sd") == values
    assert json.loads(data[PARAMETERS_KEY]) == {"model_id": "Qwen/Qwen3.5-4B"}


def test_every_template_declares_its_parameters():
    templates = Path(__file__).resolve().parents[4] / "templates"
    manifests = sorted(templates.glob("*/manifest.yaml"))
    if not manifests:
        pytest.skip("templates are not checked out beside thinkube-control")
    import yaml
    for manifest in manifests:
        declared_parameter_names(yaml.safe_load(manifest.read_text()))
