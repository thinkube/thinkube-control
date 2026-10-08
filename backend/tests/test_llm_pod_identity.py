# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A one-model backend gets one Deployment per model on a node; Ollama one per node."""

import pytest

from app.services.llm_pod_manager import LLMPodManager, MODEL_LABEL, model_slug

pods = LLMPodManager()


def test_two_models_on_one_node_have_their_own_deployment_key_and_selector():
    a, b = "nvidia/Qwen3.6-35B-A3B-NVFP4", "Qwen/Qwen3-8B"
    assert pods._make_deployment_name("vllm", "tkspark", a) != pods._make_deployment_name("vllm", "tkspark", b)
    assert pods._key("vllm", "tkspark", a) != pods._key("vllm", "tkspark", b)
    assert pods._pod_selector("vllm", "tkspark", b).endswith(f"{MODEL_LABEL}={model_slug(b)}")
    assert pods._make_deployment_name("vllm", "tkspark", b) == "vllm-inference-tkspark-qwen-qwen3-8b"
    assert pods._key("vllm", "tkspark", b) == "vllm-tkspark/qwen-qwen3-8b"


def test_a_one_model_backend_needs_the_model():
    with pytest.raises(ValueError, match="the model is required"):
        pods._make_deployment_name("vllm", "tkspark")


def test_ollama_keeps_one_deployment_per_node():
    assert pods._make_deployment_name("ollama", "tkamd2") == "ollama-tkamd2"
    assert pods._key("ollama", "tkamd2", "anything") == "ollama-tkamd2"
    assert MODEL_LABEL not in pods._pod_selector("ollama", "tkamd2")


def test_a_long_model_id_becomes_a_short_unique_name_part():
    long_a = "some-organisation/A-Very-Long-Model-Name-With-Many-Parts-v1"
    long_b = "some-organisation/A-Very-Long-Model-Name-With-Many-Parts-v2"
    slug_a, slug_b = model_slug(long_a), model_slug(long_b)
    assert len(slug_a) <= 40 and slug_a != slug_b
    assert slug_a == slug_a.lower() and all(c.isalnum() or c == "-" for c in slug_a)
    assert not slug_a.startswith("-") and not slug_a.endswith("-")
