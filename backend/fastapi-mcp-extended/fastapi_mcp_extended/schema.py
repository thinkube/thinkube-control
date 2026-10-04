# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Tool input schemas made valid JSON Schema.

fastapi-mcp gives a parameter that has an ``anyOf`` but no ``type`` the type
of its first non-null branch, and nothing else of that branch. An optional
list becomes ``{"type": "array", "anyOf": [{"type": "array", "items": ...},
{"type": "null"}]}``: an array with no ``items``, which strict clients (VS
Code's chat) refuse, together with every other tool of the server.
"""

from typing import Any


def complete_union_types(schema: Any) -> Any:
    """Give every node that has both ``type`` and ``anyOf`` the keywords of
    its ``anyOf`` branch of that type it lacks, such as ``items``. Changes
    ``schema`` in place and returns it."""
    if isinstance(schema, dict):
        branch_type = schema.get("type")
        branches = schema.get("anyOf")
        if isinstance(branch_type, str) and isinstance(branches, list):
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
