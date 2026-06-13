#!/usr/bin/env python3
"""Regression tests for parse_backend_id (backend_id -> (backend_type, node)).

The text-embeddings cases guard SP-tgjiw3: 'text-embeddings' is a compound
(hyphenated) backend type, so it must be matched as a known prefix. If it is
missing from KNOWN_BACKEND_TYPES the generic first-hyphen fallback splits
'text-embeddings-tkspark' into ('text', 'embeddings-tkspark'), which makes the
reconciler look for a deployment on a node 'embeddings-tkspark' that does not
exist -> "Deployment disappeared" -> the model never reaches `available`.
"""

import pytest

from app.services.llm_lifecycle import parse_backend_id, KNOWN_BACKEND_TYPES


def test_text_embeddings_is_a_known_prefix():
    assert "text-embeddings" in KNOWN_BACKEND_TYPES


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
    ],
)
def test_parse_backend_id(backend_id, expected):
    assert parse_backend_id(backend_id) == expected
