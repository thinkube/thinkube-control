# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A venv says whether it needs a GPU; one that does is kept only on the GPU nodes."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.jupyter_servers as js
import app.api.jupyter_venvs as jv

PACKAGES = """
[base]
ipykernel
[fine-tuning]
peft
[agent-dev]
langchain
[unsloth]
unsloth
"""


@pytest.fixture
def venv_files(tmp_path, monkeypatch):
    packages = tmp_path / "venv-packages.txt"
    packages.write_text(PACKAGES)
    variables = tmp_path / "venvs.yaml"
    variables.write_text("jupyter_venvs_version: v0.1.0\nvenv_gpu_sections:\n  - fine-tuning\n  - unsloth\n")
    monkeypatch.setattr(jv, "VENV_PACKAGES_FILE", packages)
    monkeypatch.setattr(jv, "VENV_VARS_FILE", variables)
    monkeypatch.setattr(jv.read_package_sections, "__defaults__", (packages,))
    monkeypatch.setattr(jv.gpu_sections, "__defaults__", (variables,))
    return variables


def test_a_template_needs_a_gpu_when_one_of_its_sections_does(venv_files):
    templates = jv.venv_templates()
    assert templates["fine-tuning"]["needs_gpu"] is True
    assert templates["fine-tuning-unsloth"]["needs_gpu"] is True
    assert templates["agent-dev"]["needs_gpu"] is False


def test_a_missing_gpu_list_stops_with_its_name(venv_files):
    venv_files.write_text("jupyter_venvs_version: v0.1.0\n")
    with pytest.raises(ValueError, match="venv_gpu_sections"):
        jv.venv_templates()


class NoVenvs:
    def query(self, model):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return None


def create(request):
    with pytest.raises(HTTPException) as e:
        asyncio.run(jv.create_jupyter_venv(request, db=NoVenvs(), current_user={}))
    return e.value


def test_a_venv_without_a_template_must_say_whether_it_needs_a_gpu(venv_files):
    e = create(jv.CreateVenvRequest(name="tools", packages=["numpy"]))
    assert e.status_code == 400 and "needs_gpu is required" in e.detail


def test_a_venv_from_a_template_cannot_contradict_it(venv_files):
    e = create(jv.CreateVenvRequest(name="mine", parent_template="fine-tuning", needs_gpu=False))
    assert e.status_code == 400 and "fine-tuning needs a GPU" in e.detail


class OneVenv(NoVenvs):
    def __init__(self, venv):
        self.venv = venv

    def first(self):
        return self.venv


def check_on(monkeypatch, venv, node_gpus):
    async def nodes():
        return {"tkamd1": {"capacity": {}}}

    monkeypatch.setattr(js, "_nodes", nodes)
    monkeypatch.setattr(js.res, "node_capacity", lambda info: {"gpus": node_gpus})
    return asyncio.run(js._check_kernel_fits_node(OneVenv(venv), "meepsquared", "tkamd1"))


def test_a_gpu_venv_is_refused_on_a_node_without_a_gpu(monkeypatch):
    with pytest.raises(HTTPException) as e:
        check_on(monkeypatch, SimpleNamespace(needs_gpu=True), 0)
    assert e.value.status_code == 422 and "tkamd1 has no Thinkube GPU" in e.value.detail


def test_a_gpu_venv_runs_on_a_gpu_node_and_any_other_venv_anywhere(monkeypatch):
    check_on(monkeypatch, SimpleNamespace(needs_gpu=True), 1)
    check_on(monkeypatch, SimpleNamespace(needs_gpu=False), 0)
    check_on(monkeypatch, None, 0)
