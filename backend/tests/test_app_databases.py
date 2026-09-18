#!/usr/bin/env python3
"""A deploy creates an app's missing databases and never drops one that holds the app's data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app_databases import create_statement, database_names, exists, exists_query  # noqa: E402


def test_an_app_owns_its_database_and_its_tests_own_theirs():
    assert database_names("research-debate", tests_enabled=True) == ["research_debate", "test_research_debate"]
    assert database_names("notes", tests_enabled=False) == ["notes"]


def test_a_database_that_exists_is_kept():
    assert create_statement("research_debate", "tkadmin", present=True) is None


def test_a_missing_database_is_created():
    assert create_statement("research_debate", "tkadmin", present=False) == \
        "CREATE DATABASE research_debate OWNER tkadmin;"


def test_nothing_a_deploy_runs_drops_a_database():
    statements = [create_statement("research_debate", "tkadmin", present) for present in (True, False)]

    assert not any(s and "DROP" in s.upper() for s in statements)


def test_the_answer_to_the_exists_query_is_read_as_psql_prints_it():
    assert exists_query("research_debate") == "SELECT 1 FROM pg_database WHERE datname = 'research_debate';"
    assert exists("1\n") is True
    assert exists("") is False and exists("\n") is False
