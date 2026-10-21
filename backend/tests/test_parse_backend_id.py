#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for parse_backend_id (backend_id -> (backend_type, node)).

Backend types and node names both contain hyphens, so the type is matched
against the installed backend types (the conftest fixture installs the four
the platform ships). Split on the first hyphen, 'text-embeddings-tkspark'
would name a node 'embeddings-tkspark' that does not exist, and the model
would never reach `available`.
"""

import pytest

from app.services.llm_lifecycle import is_shared_backend_id, parse_backend_id


def test_an_id_of_no_installed_type_is_returned_whole():
    assert parse_backend_id("flare-tkspark/m") == ("flare-tkspark", None)


def test_the_longest_installed_type_wins(llm_backend_types_installed):
    from app.services.llm_backend_types import BackendType

    vllm = llm_backend_types_installed["vllm"]
    llm_backend_types_installed["vllm-omni"] = BackendType(**{**vllm.__dict__, "type": "vllm-omni"})
    assert parse_backend_id("vllm-omni-tkspark/m") == ("vllm-omni", "tkspark")
    assert parse_backend_id("vllm-tkspark/m") == ("vllm", "tkspark")


def test_only_a_shared_backend_id_is_shared():
    assert is_shared_backend_id("ollama-tkamd2")
    assert not is_shared_backend_id("vllm-tkamd2/m")
    assert not is_shared_backend_id("flare-tkamd2/m")


@pytest.mark.parametrize(
    "backend_id,expected",
    [
        # The regression: compound type must not split on the first hyphen.
        ("text-embeddings-tkspark", ("text-embeddings", "tkspark")),
        ("text-embeddings", ("text-embeddings", None)),
        # Other compound / simple types stay correct.
        ("tensorrt-llm-tkspark", ("tensorrt-llm", "tkspark")),
        ("vllm-tkspark", ("vllm", "tkspark")),
        ("ollama-tkspark", ("ollama", "tkspark")),
        ("vllm", ("vllm", None)),
        # A one-model backend's id carries its model after a slash.
        ("vllm-tkspark/qwen-qwen3-8b", ("vllm", "tkspark")),
        ("text-embeddings-tkamd2/qwen-qwen3-embedding-0-6b", ("text-embeddings", "tkamd2")),
    ],
)
def test_parse_backend_id(backend_id, expected):
    assert parse_backend_id(backend_id) == expected
