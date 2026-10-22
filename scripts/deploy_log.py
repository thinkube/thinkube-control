# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The lines deploy_application.py prints, and the deployment log entry each one becomes.

The script prints every step as `[HH:MM:SS.mmm] [LEVEL] message`. The backend
reads the script's output line by line and stores every line in the
deployment log, typed by its level, so the log shows each step the deploy took.
A line not written in this form, such as a traceback, is stored as output.

A BUILD line is a fact the backend keeps on the run as well: the push that
started the build, the build's workflow, and the discovery ConfigMap the
deploy rendered with the deploy date written in it. With them a backend that started
after the script ended follows the build to its outcome.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple

LEVEL_TYPES = {
    "PHASE": "phase",
    "ERROR": "error",
    "SUCCESS": "success",
    "INFO": "info",
    "DEBUG": "debug",
    "BUILD": "info",
}

# The facts a BUILD line carries, as `<fact>=<value>`
BUILD_FACTS = ("pushed_at", "workflow", "discovery", "deployed_at")

_LINE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] \[(" + "|".join(LEVEL_TYPES) + r")\] ")


def format_line(level: str, message: str, now: datetime) -> str:
    """One line of the script's output."""
    if level not in LEVEL_TYPES:
        raise ValueError(f"log level {level!r} is not one of {sorted(LEVEL_TYPES)}")
    return f"[{now.strftime('%H:%M:%S.%f')[:-3]}] [{level}] {message}"


def entry_type(line: str) -> str:
    """The deployment log type of one line of the script's output."""
    match = _LINE.match(line)
    return LEVEL_TYPES[match.group(1)] if match else "output"


def build_line(fact: str, value: str, now: datetime) -> str:
    """The line that hands the backend one fact about the build."""
    if fact not in BUILD_FACTS:
        raise ValueError(f"build fact {fact!r} is not one of {list(BUILD_FACTS)}")
    return format_line("BUILD", f"{fact}={value}", now)


def build_fact(line: str) -> Optional[Tuple[str, str]]:
    """The fact a BUILD line carries, or None for any other line."""
    match = _LINE.match(line)
    if not match or match.group(1) != "BUILD":
        return None
    fact, _, value = line[match.end():].partition("=")
    if fact not in BUILD_FACTS or not value:
        raise ValueError(f"a BUILD line must carry one of {list(BUILD_FACTS)} as <fact>=<value>: {line!r}")
    return fact, value
