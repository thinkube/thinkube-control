#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""MCP tool input schemas are valid JSON Schema: strict clients (VS Code's
chat) refuse a server whose tool has an array with no items."""

from fastapi_mcp_extended.schema import complete_union_types


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

