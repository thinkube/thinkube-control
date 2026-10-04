# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Credentials are blanked from run output before it is written anywhere.

A run's output goes to a log file and to the deployment's log rows, and
both are read back by people and by Claude. Two things are blanked: every
secret value the platform itself handed to the run (the become password,
tokens, a password a request carried), matched exactly; and anything that
looks like a credential in the text, by the name next to it, such as
``password: x``, ``"api_token": "x"``, ``token=x`` or ``Authorization:
Bearer x``. The line stays whole; only the value becomes ``***``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

MASK = "***"

# Names that mark the value beside them as a credential.
_NAMES = r"(?:pass(?:word|wd|phrase)?|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|credentials?|become_pass|ssh_pass)"

_PATTERNS = [
    # key: value, key=value, "key": "value", key => value; the value may be quoted.
    re.compile(r"(?i)((?:[\w.-]*" + _NAMES + r"[\w.-]*)\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}\]]+)"),
    # Authorization headers of any scheme.
    re.compile(r"(?i)(authorization\s*[:=]\s*[\"']?\s*(?:bearer|basic|token)\s+)([^\s\"',;}\]]+)"),
    # A password inside a URL: scheme://user:password@host
    re.compile(r"(?i)(://[^/\s:@]+:)([^@\s/]+)(@)"),
]

# Keys of injected variables whose values are secrets, by name.
_SECRET_KEY = re.compile(r"(?i)" + _NAMES)


class Scrubber:
    def __init__(self, values: Iterable[str] = ()):
        # Longest first, so a value that contains another is blanked whole.
        self._values = sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True)

    @classmethod
    def for_run(cls, extra_vars: Optional[Dict[str, Any]] = None, env: Optional[Dict[str, str]] = None) -> "Scrubber":
        """A scrubber for one run: the secret-named values among its variables and environment."""
        values: List[str] = []
        for source in (extra_vars or {}, env or {}):
            for key, value in source.items():
                if isinstance(value, str) and _SECRET_KEY.search(str(key)):
                    values.append(value)
        return cls(values)

    def clean(self, text: str) -> str:
        if not text:
            return text
        for value in self._values:
            if value in text:
                text = text.replace(value, MASK)
        for pattern in _PATTERNS:
            if pattern.groups == 3:
                text = pattern.sub(lambda m: m.group(1) + MASK + m.group(3), text)
            else:
                text = pattern.sub(lambda m: m.group(1) + MASK, text)
        return text
