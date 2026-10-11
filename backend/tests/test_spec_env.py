#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A spec.env variable gets the value given at deploy, else its default, else the deploy stops."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import spec_env  # noqa: E402

SPEC = {"spec": {"env": [
    {"name": "GATEWAY_URL"},
    {"name": "WORKERS", "default": 4},
    {"name": "MODE", "default": "fast"},
]}}


def test_a_given_value_wins_over_the_default_and_defaults_fill_the_rest():
    given = spec_env.given_values(SPEC, {"GATEWAY_URL": "https://storage.thinkube.com", "MODE": "slow", "other": "x"})
    assert given == {"GATEWAY_URL": "https://storage.thinkube.com", "MODE": "slow"}
    assert spec_env.resolve(SPEC, given, "app") == {
        "GATEWAY_URL": "https://storage.thinkube.com", "WORKERS": "4", "MODE": "slow",
    }


def test_an_empty_value_counts_as_not_given():
    assert spec_env.given_values(SPEC, {"GATEWAY_URL": "", "MODE": None}) == {}


def test_a_required_variable_given_no_value_stops_the_deploy_naming_it():
    with pytest.raises(ValueError, match="GATEWAY_URL") as e:
        spec_env.resolve(SPEC, {}, "texplitter")
    assert "WORKERS" not in str(e.value)


def test_the_given_values_round_trip_through_the_config_map():
    given = {"GATEWAY_URL": "https://storage.thinkube.com"}
    assert spec_env.recorded({spec_env.ENV_KEY: spec_env.encode(given)}) == given


def test_an_app_deployed_before_the_values_were_recorded_has_none():
    assert spec_env.recorded({"parameters": "{}"}) == {}


def test_a_variable_a_dependency_wires_is_left_to_the_dependency():
    spec = {"spec": {
        "dependencies": [{"name": "storage", "type": "seaweedfs", "env": "GATEWAY_URL"}],
        "env": [{"name": "GATEWAY_URL"}, {"name": "MODE", "default": "fast"}],
    }}
    assert spec_env.resolve(spec, {}, "app") == {"MODE": "fast"}


def test_no_spec_env_is_nothing_to_resolve():
    assert spec_env.resolve({"spec": {}}, {}, "app") == {}
