#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""What a deploy does to an app's databases: create the ones missing, keep the ones that exist.

A deploy runs again for every redeploy of an app: a new image, a changed
setting, a component checked out again. The app's database holds what the
app has recorded — its users' data, its run history — and none of that is in
the repository the deploy renders from, so a deploy that drops the database
destroys data nothing can rebuild. A database that exists is therefore kept,
and only a missing one is created.
"""

from __future__ import annotations

from typing import Optional


def database_names(app_name: str, tests_enabled: bool) -> list[str]:
    """The databases an app owns: its own, and its tests' own when its tests need one."""
    name = app_name.replace("-", "_")
    return [name, f"test_{name}"] if tests_enabled else [name]


def exists_query(name: str) -> str:
    """The query that answers whether a database exists, as one row with 1 or no row."""
    return f"SELECT 1 FROM pg_database WHERE datname = '{name}';"


def exists(output: str) -> bool:
    """Whether `psql -tA` answered the exists query with a row."""
    return output.strip() == "1"


def create_statement(name: str, owner: str, present: bool) -> Optional[str]:
    """The statement a deploy runs for one database: CREATE when it is missing, nothing when it exists."""
    if present:
        return None
    return f"CREATE DATABASE {name} OWNER {owner};"
