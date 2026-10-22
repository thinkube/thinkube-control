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


def test_a_build_fact_is_read_back_from_its_line():
    from deploy_log import build_fact, build_line

    line = build_line("workflow", "llama-cpp-build-frc75", NOW)
    assert entry_type(line) == "info"
    assert build_fact(line) == ("workflow", "llama-cpp-build-frc75")


def test_a_value_may_hold_the_separator():
    from deploy_log import build_fact, build_line

    assert build_fact(build_line("pushed_at", "2026-10-22T11:00:00+00:00", NOW)) == ("pushed_at", "2026-10-22T11:00:00+00:00")


def test_other_lines_carry_no_fact():
    from deploy_log import build_fact

    assert build_fact(format_line("INFO", "workflow=x", NOW)) is None
    assert build_fact("workflow=x") is None


def test_an_unknown_fact_is_refused():
    from deploy_log import build_fact, build_line

    with pytest.raises(ValueError, match="commit"):
        build_line("commit", "abc", NOW)
    with pytest.raises(ValueError, match="BUILD line"):
        build_fact("[08:54:36.123] [BUILD] commit=abc")
