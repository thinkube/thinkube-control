#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Every line the deploy script prints reaches the deployment log, typed by its level."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from deploy_log import entry_type, format_line  # noqa: E402

NOW = datetime(2026, 9, 18, 8, 54, 36, 123456)


def test_each_level_becomes_its_own_type():
    assert entry_type(format_line("PHASE", "🚀 PHASE 1: Setup", NOW)) == "phase"
    assert entry_type(format_line("ERROR", "Deployment failed", NOW)) == "error"
    assert entry_type(format_line("SUCCESS", "✅ Phase 1 complete", NOW)) == "success"
    assert entry_type(format_line("DEBUG", "Deployment ID: x", NOW)) == "debug"


def test_info_lines_are_kept_whatever_words_they_hold():
    for message in ("Kept database research_debate: it exists and holds the app's data",
                    "Dropped database research_debate",
                    "Reset checkout /home/thinkube/apps/notes with git reset --hard origin/main: from a to b"):
        assert entry_type(format_line("INFO", message, NOW)) == "info"


def test_a_word_inside_the_message_does_not_change_the_type():
    assert entry_type(format_line("INFO", "a PHASE, an ERROR and a SUCCESS", NOW)) == "info"


def test_lines_the_script_did_not_format_are_output():
    assert entry_type("Traceback (most recent call last):") == "output"
    assert entry_type("RuntimeError: Could not fetch") == "output"


def test_the_line_carries_the_time_and_level():
    assert format_line("INFO", "hello", NOW) == "[08:54:36.123] [INFO] hello"


def test_an_unknown_level_is_refused_where_the_line_is_written():
    with pytest.raises(ValueError, match="WARNING"):
        format_line("WARNING", "x", NOW)
