# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""How the columns of a dataset become the examples a model trains on.

A mapping names the kind of rows and the columns that hold each part:

- messages:    {"messages": col}, a list of {"role", "content"}
- sharegpt:    {"conversations": col}, a list of {"from", "value"}
- prompt:      {"prompt": col, "completion": col, "system": col or absent}
- instruction: {"instruction": col, "input": col or absent, "output": col}
- text:        {"text": col}

Every kind but text becomes chat turns, {"messages": [...]}, which the
trainer formats with the model's chat template. text is trained on as it is.
"""

from typing import Any, Dict, List, Optional

KINDS = {
    "messages": ({"messages"}, set()),
    "sharegpt": ({"conversations"}, set()),
    "prompt": ({"prompt", "completion"}, {"system"}),
    "instruction": ({"instruction", "output"}, {"input"}),
    "text": ({"text"}, set()),
}
ROLES = {"system", "user", "assistant", "tool"}
SHAREGPT_ROLES = {"system": "system", "human": "user", "user": "user", "gpt": "assistant",
                  "assistant": "assistant", "tool": "tool"}

# Column names a dataset commonly uses for each part, in the order they are tried
GUESSES = [
    ("messages", {"messages": ["messages"]}),
    ("sharegpt", {"conversations": ["conversations", "conversation"]}),
    ("prompt", {"prompt": ["prompt"], "completion": ["completion", "response", "chosen"]}),
    ("instruction", {"instruction": ["instruction"], "output": ["output", "response"], "input": ["input", "context"]}),
    ("prompt", {"prompt": ["question", "query", "problem"], "completion": ["answer", "solution", "response"]}),
    ("prompt", {"prompt": ["input", "source"], "completion": ["output", "target"]}),
    ("text", {"text": ["text", "content"]}),
]


class MappingError(ValueError):
    """A mapping that does not fit the dataset; the message says how."""


def check(mapping: Dict[str, Any], columns: List[str]) -> Dict[str, Any]:
    """The mapping, refused with the reason when it names a kind or a column that is not there."""
    kind = mapping.get("kind")
    if kind not in KINDS:
        raise MappingError(f"kind must be one of {sorted(KINDS)}")
    required, optional = KINDS[kind]
    given = {k: v for k, v in (mapping.get("columns") or {}).items() if v}
    missing = required - set(given)
    if missing:
        raise MappingError(f"a {kind} mapping needs the columns {sorted(missing)}")
    unknown = set(given) - required - optional
    if unknown:
        raise MappingError(f"a {kind} mapping has no part {sorted(unknown)}")
    absent = [c for c in given.values() if c not in columns]
    if absent:
        raise MappingError(f"the dataset has no column {absent}; it has {columns}")
    return {"kind": kind, "columns": given}


def guess(columns: List[str]) -> Optional[Dict[str, Any]]:
    """The mapping the column names suggest, or None when none fits."""
    lowered = {c.lower(): c for c in columns}
    for kind, parts in GUESSES:
        chosen = {}
        for part, names in parts.items():
            found = next((lowered[n] for n in names if n in lowered), None)
            if found:
                chosen[part] = found
        if KINDS[kind][0] <= set(chosen):
            return {"kind": kind, "columns": chosen}
    return None


def output_format(mapping: Dict[str, Any]) -> str:
    return "text" if mapping["kind"] == "text" else "messages"


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{what} is not text")
    return value


def apply(row: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """One example in the trainer's format, from one row."""
    kind, cols = mapping["kind"], mapping["columns"]
    if kind == "text":
        return {"text": _text(row.get(cols["text"]), f"column {cols['text']}")}
    if kind == "messages":
        turns = row.get(cols["messages"])
        if not isinstance(turns, list) or not turns:
            raise MappingError(f"column {cols['messages']} is not a list of turns")
        messages = []
        for turn in turns:
            if not isinstance(turn, dict) or turn.get("role") not in ROLES:
                raise MappingError(f"a turn of column {cols['messages']} has no role of {sorted(ROLES)}")
            messages.append({"role": turn["role"], "content": _text(turn.get("content"), "a turn's content")})
        return {"messages": messages}
    if kind == "sharegpt":
        turns = row.get(cols["conversations"])
        if not isinstance(turns, list) or not turns:
            raise MappingError(f"column {cols['conversations']} is not a list of turns")
        messages = []
        for turn in turns:
            role = SHAREGPT_ROLES.get(turn.get("from")) if isinstance(turn, dict) else None
            if role is None:
                raise MappingError(f"a turn of column {cols['conversations']} has no from of {sorted(SHAREGPT_ROLES)}")
            messages.append({"role": role, "content": _text(turn.get("value"), "a turn's value")})
        return {"messages": messages}
    messages = []
    if kind == "prompt":
        if cols.get("system") and row.get(cols["system"]):
            messages.append({"role": "system", "content": _text(row[cols["system"]], f"column {cols['system']}")})
        messages.append({"role": "user", "content": _text(row.get(cols["prompt"]), f"column {cols['prompt']}")})
        messages.append({"role": "assistant", "content": _text(row.get(cols["completion"]), f"column {cols['completion']}")})
    else:
        prompt = _text(row.get(cols["instruction"]), f"column {cols['instruction']}")
        extra = row.get(cols["input"]) if cols.get("input") else None
        if isinstance(extra, str) and extra.strip():
            prompt = f"{prompt}\n\n{extra}"
        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "assistant", "content": _text(row.get(cols["output"]), f"column {cols['output']}")})
    return {"messages": messages}


def preview(rows: List[Dict[str, Any]], mapping: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each row as the trainer gets it, or the reason it cannot be."""
    if mapping is None:
        return []
    out = []
    for row in rows:
        try:
            out.append({"example": apply(row, mapping)})
        except MappingError as e:
            out.append({"error": str(e)})
    return out
