#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""MCP tool input schemas are valid JSON Schema: strict clients (VS Code's
chat) refuse a server whose tool has an array with no items. A tool offers
the fields of its operation's request body, also when the body is optional."""

from typing import Optional, Union

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi_mcp.openapi.convert import convert_openapi_to_mcp_tools
from pydantic import BaseModel

from fastapi_mcp_extended.schema import complete_union_types, expose_optional_bodies


def arrays_without_items(schema, path=""):
    found = []
    if isinstance(schema, dict):
        if schema.get("type") == "array" and "items" not in schema:
            found.append(path or "/")
        for key, value in schema.items():
            found += arrays_without_items(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            found += arrays_without_items(value, f"{path}[{i}]")
    return found


def test_optional_list_gets_the_items_of_its_array_branch():
    schema = {"type": "object", "properties": {"packages": {
        "type": "array", "title": "packages",
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
    }}}
    complete_union_types(schema)
    assert arrays_without_items(schema) == []
    packages = schema["properties"]["packages"]
    assert packages["items"] == {"type": "string"}
    assert packages["type"] == "array"
    assert packages["title"] == "packages"


def test_a_keyword_already_set_is_kept():
    schema = {"type": "array", "items": {"type": "integer"},
              "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
    complete_union_types(schema)
    assert schema["items"] == {"type": "integer"}


def test_nested_schemas_are_completed():
    schema = {"type": "object", "properties": {"cells": {"type": "object", "properties": {"lines": {
        "type": "array", "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}}}}}
    complete_union_types(schema)
    assert schema["properties"]["cells"]["properties"]["lines"]["items"] == {"type": "string"}



class StartRequest(BaseModel):
    cpu_cores: Optional[int] = None
    gpus: Optional[int] = None
    memory_gb: Union[int, str, None] = None


def tools_of(app):
    openapi = get_openapi(title="t", version="1", routes=app.routes)
    tools, _ = convert_openapi_to_mcp_tools(openapi)
    expose_optional_bodies(tools, openapi)
    for tool in tools:
        complete_union_types(tool.inputSchema)
    return {t.name: t.inputSchema for t in tools}


def test_an_optional_body_offers_its_fields_as_optional_arguments():
    app = FastAPI()

    @app.post("/servers/{node}/start", operation_id="start_server")
    def start(node: str, request: Optional[StartRequest] = None):
        return {}

    schema = tools_of(app)["start_server"]
    assert set(schema["properties"]) == {"node", "cpu_cores", "gpus", "memory_gb"}
    assert schema["required"] == ["node"]
    assert schema["properties"]["gpus"]["type"] == "integer"
    # A field that takes an integer or a string accepts both.
    assert schema["properties"]["memory_gb"]["type"] == ["integer", "string"]


def test_a_required_body_is_left_as_fastapi_mcp_made_it():
    app = FastAPI()

    @app.post("/secrets", operation_id="create")
    def create(request: StartRequest):
        return {}

    schema = tools_of(app)["create"]
    assert set(schema["properties"]) == {"cpu_cores", "gpus", "memory_gb"}
    assert schema["properties"]["memory_gb"]["type"] == ["integer", "string"]
