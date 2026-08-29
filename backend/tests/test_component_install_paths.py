#!/usr/bin/env python3
"""Tests that one menu can carry two install mechanisms.

Every component is listed under Optional Components. The platform ones run an
ansible playbook; the inference backends go through the template deployment
process. The listing does not care which, so only install branches.
"""

import pytest

from app.services.optional_components import OptionalComponentService

PLAYBOOK_ENTRY = {
    "display_name": "Qdrant",
    "namespace": "qdrant",
    "playbooks": {"install": "00_install.yaml", "uninstall": "19_rollback.yaml"},
}
TEMPLATE_ENTRY = {
    "display_name": "vLLM",
    "namespace": "vllm",
    "template": {
        "url": "https://github.com/thinkube/tkt-vllm-gradio",
        "fixed_name": "vllm",
    },
}


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "app.services.optional_components.get_components_catalog",
        lambda: {"qdrant": PLAYBOOK_ENTRY, "vllm": TEMPLATE_ENTRY},
    )
    return OptionalComponentService.__new__(OptionalComponentService)


def test_a_template_component_reports_its_template(service):
    descriptor = service.template_descriptor("vllm")

    assert descriptor["url"] == "https://github.com/thinkube/tkt-vllm-gradio"
    assert descriptor["fixed_name"] == "vllm"


def test_a_playbook_component_has_no_template(service):
    assert service.template_descriptor("qdrant") is None


def test_an_unknown_component_has_no_template(service):
    assert service.template_descriptor("nope") is None


def test_a_template_component_has_no_playbook(service):
    """Looking one up must return None, not raise on the missing key."""
    assert service.get_playbook_path("vllm", "install") is None
    assert service.get_playbook_path("vllm", "uninstall") is None


def test_a_playbook_component_still_resolves_its_playbook(service):
    path = service.get_playbook_path("qdrant", "install")

    assert path == "ansible/40_thinkube/optional/qdrant/00_install.yaml"


def test_a_descriptor_without_a_url_is_not_usable(monkeypatch):
    monkeypatch.setattr(
        "app.services.optional_components.get_components_catalog",
        lambda: {"broken": {"template": {"fixed_name": "broken"}}},
    )
    s = OptionalComponentService.__new__(OptionalComponentService)

    assert s.template_descriptor("broken") is None


def test_the_two_kinds_are_mutually_exclusive(service):
    """Each component installs exactly one way."""
    for name in ("qdrant", "vllm"):
        by_template = service.template_descriptor(name) is not None
        by_playbook = service.get_playbook_path(name, "install") is not None
        assert by_template != by_playbook, name
