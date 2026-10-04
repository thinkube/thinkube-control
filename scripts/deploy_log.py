# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The lines deploy_application.py prints, and the deployment log entry each one becomes.

The script prints every step as `[HH:MM:SS.mmm] [LEVEL] message`. The backend
reads the script's output line by line and stores every line in the
deployment log, typed by its level, so the log shows each step the deploy took.
A line not written in this form, such as a traceback, is stored as output.
"""

from __future__ import annotations

import re
from datetime import datetime

LEVEL_TYPES = {
    "PHASE": "phase",
    "ERROR": "error",
    "SUCCESS": "success",
    "INFO": "info",
    "DEBUG": "debug",
}

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
