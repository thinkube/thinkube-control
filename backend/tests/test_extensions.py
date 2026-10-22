# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An extension's declaration is taken as Control uses it, or refused with the field and why."""

import pytest

from app.services.extensions import check_extension, problems

DECLARED = {
    "title": "Forge",
    "description": "Fine-tune language models",
    "icon": "flame",
    "platform": "0.1",
    "depends": ["datasets"],
    "ui": {"module": "/extension/index.js", "css": "/extension/index.css"},
    "nav": [{"label": "Runs", "path": "/", "icon": "list-checks"}, {"label": "New fine-tune", "path": "/runs/new"}],
}


def test_a_complete_declaration_is_taken_with_the_module_folder():
    extension, error = check_extension(DECLARED)
    assert error is None
    assert extension["ui"] == {"module": "/extension/index.js", "css": "/extension/index.css", "folder": "/extension/"}
    assert [e["path"] for e in extension["nav"]] == ["/", "/runs/new"]
    assert extension["nav"][1]["icon"] is None


@pytest.mark.parametrize("change, field", [
    ({"title": " "}, "spec.extension.title"),
    ({"ui": {"css": "/extension/index.css"}}, "spec.extension.ui.module"),
    ({"ui": {"module": "extension/index.js"}}, "spec.extension.ui.module"),
    ({"ui": {"module": "/extension/index.js", "css": "/other/index.css"}}, "spec.extension.ui.css"),
    ({"nav": []}, "spec.extension.nav"),
    ({"nav": [{"label": "Runs", "path": "/../admin"}]}, "spec.extension.nav[0].path"),
    ({"platform": "0.1.3"}, "spec.extension.platform"),
    ({"depends": ["Datasets"]}, "spec.extension.depends"),
])
def test_a_declaration_with_a_wrong_field_is_refused_naming_it(change, field):
    extension, error = check_extension({**DECLARED, **change})
    assert extension is None
    assert field in error


def test_an_extension_for_this_platform_with_its_depends_installed_has_no_problems():
    extension, _ = check_extension(DECLARED)
    assert problems(extension, ["forge", "datasets"], "0.1") == []


def test_another_platform_and_a_missing_depend_are_named():
    extension, _ = check_extension(DECLARED)
    found = problems(extension, ["forge"], "0.2")
    assert found == ["built for platform 0.1; this is platform 0.2", "needs datasets, which is not installed"]
