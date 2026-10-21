#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The two placeholders of thinkube.yaml are replaced in every string, at any depth."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from thinkube_yaml_placeholders import substitute  # noqa: E402


def test_both_placeholders_are_replaced_at_any_depth():
    config = {
        "metadata": {"name": "{{ project_name }}"},
        "spec": {
            "env": [{"name": "BASE_URL", "default": "https://llm.{{ domain_name }}/v1"}],
            "deploy": {"at": "https://{{ project_name }}.{{ domain_name }}"},
        },
    }
    result = substitute(config, "dentalpin", "thinkube.com")
    assert result["metadata"]["name"] == "dentalpin"
    assert result["spec"]["env"][0]["default"] == "https://llm.thinkube.com/v1"
    assert result["spec"]["deploy"]["at"] == "https://dentalpin.thinkube.com"


def test_values_that_are_not_strings_and_other_braces_are_kept():
    config = {"replicas": 2, "enabled": True, "note": "{{ other }} stays", "list": [1, None]}
    assert substitute(config, "a", "b") == config
