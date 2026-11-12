# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An example as the base model sees it: its chat turns through the model's chat template.

A model keeps its template in tokenizer_config.json ("chat_template") or in
chat_template.jinja beside it. The template is rendered as transformers
renders it: a sandboxed Jinja environment that trims blocks, with tojson,
raise_exception and strftime_now, and the tokenizer's special tokens.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

from jinja2 import TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment


class TemplateMissing(Exception):
    """The model has no chat template."""


def _token(config: Dict[str, Any], name: str) -> str:
    value = config.get(name)
    if isinstance(value, dict):
        return value.get("content", "")
    return value or ""


def template_of(tokenizer_config: Optional[str], template_file: Optional[str]) -> Dict[str, Any]:
    """The chat template and the special tokens, from the model's files."""
    config = json.loads(tokenizer_config) if tokenizer_config else {}
    template = template_file or config.get("chat_template")
    if isinstance(template, list):
        template = next((t["template"] for t in template if t.get("name") == "default"), None)
    if not template:
        raise TemplateMissing("the model has no chat template; train it on text, not on chat turns")
    return {"template": template, "bos_token": _token(config, "bos_token"), "eos_token": _token(config, "eos_token")}


def _raise(message: str):
    raise TemplateError(message)


def render(chat: Dict[str, Any], messages: list) -> str:
    """The text the model trains on for one example."""
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
    env.filters["tojson"] = lambda value, indent=None, **_: json.dumps(value, ensure_ascii=False, indent=indent)
    env.globals["raise_exception"] = _raise
    env.globals["strftime_now"] = lambda fmt: datetime.now().strftime(fmt)
    return env.from_string(chat["template"]).render(
        messages=messages, add_generation_prompt=False, bos_token=chat["bos_token"], eos_token=chat["eos_token"],
    )
