#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A template is a component template when the template catalog says so, or
when a component of the components catalog installs from it (a user
component is listed there only)."""

from app.api import templates
from app.services import optional_components

APP = {"name": "tkt-notes", "github_url": "https://github.com/someone/tkt-notes", "deployment_type": "app"}
COMPONENTS = {"apertium": {"namespace": "apertium",
                           "template": {"url": "https://github.com/kubexlat/tkt-apertium", "fixed_name": "apertium"}}}


def setup(monkeypatch):
    monkeypatch.setattr(templates, "fetch_merged_catalog", lambda **kw: [APP])
    monkeypatch.setattr(optional_components, "get_components_catalog", lambda: COMPONENTS)


def test_the_template_catalog_entry_is_used_when_it_lists_the_template(monkeypatch):
    setup(monkeypatch)
    assert templates.catalog_template("https://github.com/someone/tkt-notes/") == APP


def test_a_user_component_template_is_a_component_under_its_fixed_name(monkeypatch):
    setup(monkeypatch)
    entry = templates.catalog_template("https://github.com/kubexlat/tkt-apertium")
    assert entry["deployment_type"] == "component" and entry["fixed_name"] == "apertium"
    assert templates.deployment_type("https://github.com/kubexlat/tkt-apertium") == "component"


def test_a_template_no_catalog_lists_is_none(monkeypatch):
    setup(monkeypatch)
    assert templates.catalog_template("https://github.com/someone/other") is None
