# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The served context of a model, as each engine's /v1/models reports it."""

from app.services.llm_backend_discovery import _served_context


def test_vllm_and_flare_report_max_model_len():
    assert _served_context({"id": "m", "max_model_len": 32768}) == 32768


def test_llama_server_reports_meta_n_ctx():
    card = {"id": "m", "meta": {"n_ctx": 8192, "n_ctx_train": 262144}}
    assert _served_context(card) == 8192


def test_a_card_without_either_has_no_served_context():
    assert _served_context({"id": "m"}) is None
