#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Each gateway alias, and the agent default, belongs to one model of the catalog.

The platform catalog and the user's metadata repository are merged. A claim
from the user's repository wins over the platform's; two claims from one
source are an error that names both entries.
"""

import pytest

from app.services.metadata_fetcher import CatalogUnavailableError
from app.services.model_downloader import resolve_names


def model(model_id, source="platform", **fields):
    return {"id": model_id, "_source": source, **fields}


AGENT = {"capabilities": ["agent"], "context_length": 262144, "max_output": 32768}


def test_a_catalog_with_one_owner_per_name_is_kept():
    entries = [
        model("a/fast", aliases=["thinkube-fast"], default_agent=True, **AGENT),
        model("a/deep", aliases=["thinkube-deep"], **AGENT),
    ]
    assert resolve_names(entries) == entries


def test_the_users_alias_wins_and_the_platform_entry_loses_it():
    platform = model("a/fast", aliases=["thinkube-fast", "fast"])
    user = model("u/mine", "user", aliases=["thinkube-fast"])
    resolved = {e["id"]: e for e in resolve_names([platform, user])}
    assert resolved["a/fast"]["aliases"] == ["fast"]
    assert resolved["u/mine"]["aliases"] == ["thinkube-fast"]
    assert platform["aliases"] == ["thinkube-fast", "fast"], "the cached catalog is not changed"


def test_two_platform_entries_with_one_alias_are_refused():
    with pytest.raises(CatalogUnavailableError, match="a/one, a/two"):
        resolve_names([model("a/one", aliases=["x"]), model("a/two", aliases=["x"])])


def test_an_alias_that_is_a_model_id_is_refused():
    with pytest.raises(CatalogUnavailableError, match="is the id of another model"):
        resolve_names([model("a/one", aliases=["a/two"]), model("a/two")])


def test_the_users_default_wins_over_the_platforms():
    platform = model("a/fast", default_agent=True, **AGENT)
    user = model("u/mine", "user", default_agent=True, **AGENT)
    resolved = {e["id"]: e for e in resolve_names([platform, user])}
    assert resolved["a/fast"]["default_agent"] is False
    assert resolved["u/mine"]["default_agent"] is True


def test_two_defaults_from_one_source_are_refused():
    with pytest.raises(CatalogUnavailableError, match="a/one, a/two"):
        resolve_names([
            model("a/one", default_agent=True, **AGENT),
            model("a/two", default_agent=True, **AGENT),
        ])


def test_the_default_must_be_an_agent_model():
    with pytest.raises(CatalogUnavailableError, match="not marked agent"):
        resolve_names([model("a/one", default_agent=True)])


def test_an_agent_model_needs_its_context_and_answer_budget():
    with pytest.raises(CatalogUnavailableError, match="no context_length or max_output"):
        resolve_names([model("a/one", capabilities=["agent"])])
