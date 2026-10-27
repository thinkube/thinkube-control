# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A fine-tune's catalogue entry is checked, then written to the user's
metadata repository in place of an entry with the same id."""

import base64
import json

import httpx
import pytest

from app.services import finetuned_models as fm

ENTRY = {
    "id": "forge/greeter", "name": "Greeter", "params_b": 2.25, "active_params_b": None, "quantization": "BF16",
    "context_length": 8192, "description": "d", "server_type": ["vllm"], "task": "text-generation",
    "reasoning_format": None, "tool_use": False, "stop_tokens": [], "license": "apache-2.0", "gated": False,
    "serving_name": "forge/greeter", "is_finetuned": True,
}


def test_an_entry_missing_fields_is_refused():
    with pytest.raises(fm.FinetunedModelError) as e:
        fm.check_entry({"id": "x"})
    assert e.value.status == 422 and "name" in str(e.value)


def test_an_entry_served_by_vllm_in_another_quantization_is_refused():
    with pytest.raises(fm.FinetunedModelError) as e:
        fm.check_entry({**ENTRY, "quantization": "FP8"})
    assert "quantization" in str(e.value)


def test_an_nvfp4_entry_for_vllm_is_accepted():
    fm.check_entry({**ENTRY, "quantization": "NVFP4"})


def test_a_gguf_export_is_served_by_llama_cpp():
    fm.check_entry({**ENTRY, "id": "forge/greeter-gguf", "serving_name": "forge/greeter-gguf",
                    "server_type": ["llama-cpp"], "quantization": "Q8_0"})


def test_a_gguf_entry_for_vllm_or_with_another_quantization_is_refused():
    with pytest.raises(fm.FinetunedModelError, match="vllm serves a fine-tune as"):
        fm.check_entry({**ENTRY, "quantization": "Q8_0"})
    with pytest.raises(fm.FinetunedModelError, match="llama-cpp serves a fine-tune as"):
        fm.check_entry({**ENTRY, "server_type": ["llama-cpp"], "quantization": "Q4_K_M"})
    with pytest.raises(fm.FinetunedModelError, match="server_type"):
        fm.check_entry({**ENTRY, "server_type": ["vllm", "llama-cpp"]})


class GitHub:
    """The GitHub contents API of one repository holding models.json."""

    def __init__(self, models):
        self.models = models
        self.written = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/alice/alice-metadata":
            return httpx.Response(200, json={})
        if request.method == "GET":
            content = base64.b64encode(json.dumps({"models": self.models}).encode()).decode()
            return httpx.Response(200, json={"content": content, "sha": "s1"})
        body = json.loads(request.content)
        self.written = (json.loads(base64.b64decode(body["content"])), body["sha"])
        return httpx.Response(200, json={})


@pytest.fixture
def github(monkeypatch):
    def make(models):
        fake = GitHub(models)
        real = httpx.Client
        monkeypatch.setattr(fm, "_get_github_config", lambda: ("alice", "token"))
        monkeypatch.setattr(fm.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw))
        return fake
    return make


def test_the_entry_replaces_one_with_the_same_id_and_keeps_the_others(github):
    fake = github([{"id": "other/model"}, {"id": "forge/greeter", "name": "old"}])
    assert fm.write_entry(ENTRY) == "alice/alice-metadata"
    catalog, sha = fake.written
    assert sha == "s1"
    assert catalog["models"] == [{"id": "other/model"}, ENTRY]


def test_an_entry_already_there_is_not_written_again(github):
    fake = github([ENTRY])
    fm.write_entry(ENTRY)
    assert fake.written is None
