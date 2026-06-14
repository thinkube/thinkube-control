#!/usr/bin/env python3
"""Auxiliary-role (drafter) handling tests (SP-tgkm1m, SL-1).

A DFlash speculative-decoding drafter is catalogued and mirrored so the gateway
can locate it, but it must never be offered as a standalone loadable model — it
only runs inside a target model's vLLM via --speculative-config. These tests pin
the contract: role defaults to "primary", and the loadable-list filter (mirrored
verbatim from the get_llm_models endpoint) drops non-primary roles.
"""

from app.api.llm.schemas import ModelEntry


def _m(**kw) -> ModelEntry:
    base = dict(id="x", name="x")
    base.update(kw)
    return ModelEntry(**base)


def test_role_defaults_to_primary():
    assert _m().role == "primary"


def test_drafter_role_round_trips():
    assert _m(id="z-lab/Qwen3.6-27B-DFlash", role="drafter").role == "drafter"


def test_loadable_filter_excludes_non_primary():
    # Mirrors the predicate in app/api/llm/models_api.list_models.
    models = [
        _m(id="chat", role="primary"),
        _m(id="drafter", role="drafter"),
    ]
    loadable = [m for m in models if m.role == "primary"]
    assert [m.id for m in loadable] == ["chat"]
