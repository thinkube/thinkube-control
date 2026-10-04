# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A first template deploy refuses optional components; a redeploy renders them.

vllm, tensorrt and text-embeddings are templates the catalog marks as components.
Optional Components installs them under their fixed name. A deploy link to the
Templates page must not create a second copy under another name.
"""

import asyncio

import pytest
from fastapi import HTTPException

import app.api.templates as templates
from app.models.deployment_schemas import TemplateDeployAsyncRequest

VLLM = {
    "name": "tkt-vllm-gradio",
    "fixed_name": "vllm",
    "deployment_type": "component",
    "github_url": "https://github.com/thinkube/tkt-vllm-gradio",
}
WEBAPP = {
    "name": "tkt-webapp-react-fastapi",
    "deployment_type": "app",
    "github_url": "https://github.com/thinkube/tkt-webapp-react-fastapi",
}


class ReachedNameCheck(Exception):
    """Raised where the deploy checks the service name, after the component check."""


class StopAtNameCheck:
    def __init__(self, db):
        pass

    def validate_service_name(self, name, service_type):
        raise ReachedNameCheck(f"{name}:{service_type}")


@pytest.fixture
def catalog(monkeypatch):
    entries = {e["github_url"]: e for e in (VLLM, WEBAPP)}
    monkeypatch.setattr(templates, "catalog_template", lambda url: entries.get(url.rstrip("/")))
    monkeypatch.setattr(templates, "DependencyManager", StopAtNameCheck)


def request(url, name):
    return TemplateDeployAsyncRequest(template_url=url, template_name=name, variables={})


def deploy(req):
    return asyncio.run(templates.deploy_template_async(req, None, db=None, current_user={}))


def redeploy(req):
    return asyncio.run(templates.redeploy_template_async(req, None, db=None, current_user={}))


def test_first_deploy_of_a_component_is_refused(catalog):
    with pytest.raises(HTTPException) as refused:
        deploy(request(VLLM["github_url"], "my-vllm"))

    assert refused.value.status_code == 400
    assert "optional component 'vllm'" in refused.value.detail
    assert "Optional Components" in refused.value.detail


def test_first_deploy_of_a_component_under_its_fixed_name_is_refused(catalog):
    with pytest.raises(HTTPException) as refused:
        deploy(request(VLLM["github_url"], "vllm"))

    assert refused.value.status_code == 400


def test_redeploy_of_a_component_passes_the_component_check(catalog):
    with pytest.raises(HTTPException) as stopped:
        redeploy(request(VLLM["github_url"], "vllm"))

    assert "vllm:component" in stopped.value.detail


def test_first_deploy_of_an_application_template_passes_the_component_check(catalog):
    with pytest.raises(HTTPException) as stopped:
        deploy(request(WEBAPP["github_url"], "my-app"))

    assert "my-app:user_app" in stopped.value.detail
