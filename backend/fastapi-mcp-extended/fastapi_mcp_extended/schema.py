# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Tool input schemas made valid JSON Schema, with every body field offered.

fastapi-mcp gives a parameter that has an ``anyOf`` but no ``type`` the type
of its first non-null branch, and nothing else of that branch. An optional
list becomes ``{"type": "array", "anyOf": [{"type": "array", "items": ...},
{"type": "null"}]}``: an array with no ``items``, which strict clients (VS
Code's chat) refuse, together with every other tool of the server.
"""

from typing import Any, Dict, List, Optional

from fastapi_mcp.openapi.utils import get_single_param_type_from_schema, resolve_schema_references


def complete_union_types(schema: Any) -> Any:
    """Give every node that has both ``type`` and ``anyOf`` the keywords of
    its ``anyOf`` branch of that type it lacks, such as ``items``; a node
    whose branches have several types gets all of them as its ``type``.
    Changes ``schema`` in place and returns it."""
    if isinstance(schema, dict):
        branch_type = schema.get("type")
        branches = schema.get("anyOf")
        if isinstance(branch_type, str) and isinstance(branches, list):
            types = [b["type"] for b in branches
                     if isinstance(b, dict) and b.get("type") not in (None, "null")]
            if len(set(types)) > 1:
                # type is checked together with anyOf: a field that takes an
                # integer or a string must name both, or 1 is refused.
                schema["type"] = list(dict.fromkeys(types))
            else:
                branch = next((b for b in branches if isinstance(b, dict) and b.get("type") == branch_type), None)
                if branch is not None:
                    for key, value in branch.items():
                        schema.setdefault(key, value)
        for value in schema.values():
            complete_union_types(value)
    elif isinstance(schema, list):
        for value in schema:
            complete_union_types(value)
    return schema


def _body_schema(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = operation.get("requestBody", {}).get("content", {})
    media = next(iter(content.values()), None)
    return media.get("schema") if media else None


def expose_optional_bodies(tools: List[Any], openapi_schema: Dict[str, Any]) -> None:
    """Add the fields of an optional request body to its tool's input schema.

    fastapi-mcp reads body fields only when the body schema has ``properties``
    at its top. FastAPI writes an optional body as ``anyOf: [<model>, null]``,
    so a tool such as ``start_notebook_server`` offered none of its body's
    fields. The fields are added as optional arguments; fastapi-mcp sends every
    argument that is not a path, query or header parameter as the JSON body.
    Changes the tools' input schemas in place.
    """
    resolved = resolve_schema_references(openapi_schema, openapi_schema)
    operations = {
        operation["operationId"]: operation
        for methods in resolved.get("paths", {}).values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    for tool in tools:
        body = _body_schema(operations.get(tool.name, {}))
        if not body or "properties" in body:
            continue
        model = next((b for b in body.get("anyOf", []) if isinstance(b, dict) and "properties" in b), None)
        if model is None:
            continue
        properties = tool.inputSchema.setdefault("properties", {})
        for name, field in model["properties"].items():
            if name in properties:
                raise ValueError(
                    f"Tool {tool.name}: body field '{name}' has the name of a path or query parameter"
                )
            field = field.copy()
            field.setdefault("title", name)
            if "type" not in field:
                field["type"] = get_single_param_type_from_schema(field)
            properties[name] = field

