# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""API token authentication never waits for a database connection on the
event loop, and leaves the request holding no connection."""

import asyncio
import inspect
from datetime import datetime, timedelta

from fastapi.security import HTTPAuthorizationCredentials

import app.core.api_tokens as api_tokens


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args):
        return self

    def first(self):
        return self.row


class _Session:
    """Records the order of reads and transaction ends."""

    def __init__(self, row):
        self.row = row
        self.events = []
        self.thread = None

    def query(self, model):
        import threading

        self.thread = threading.current_thread()
        return _Query(self.row)

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")


class _Row:
    def __init__(self, session, expires_at=None):
        self._session = session
        self.expires_at = expires_at
        self.last_used = None

    def __getattr__(self, name):
        values = {"user_id": "u1", "username": "thinkube", "id": "t1", "name": "ide", "scopes": ["all"]}
        if name in values:
            self._session.events.append(f"read {name}")
            return values[name]
        raise AttributeError(name)


def _credentials():
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="tk_example")


def test_lookup_runs_in_a_worker_thread():
    src = inspect.getsource(api_tokens.verify_api_token)
    assert "asyncio.to_thread(_lookup_api_token" in src

    import threading

    db = _Session(None)
    asyncio.run(api_tokens.verify_api_token(_credentials(), db))
    assert db.thread is not threading.main_thread()


def test_user_is_read_before_the_commit():
    db = _Session(None)
    db.row = _Row(db)
    user = asyncio.run(api_tokens.verify_api_token(_credentials(), db))

    assert user["preferred_username"] == "thinkube" and user["auth_method"] == "api_token"
    assert db.events[-1] == "commit"
    assert all(e.startswith("read") for e in db.events[:-1])


def test_unknown_or_expired_token_ends_the_transaction():
    db = _Session(None)
    assert asyncio.run(api_tokens.verify_api_token(_credentials(), db)) is None
    assert db.events == ["rollback"]

    db = _Session(None)
    db.row = _Row(db, expires_at=datetime.utcnow() - timedelta(days=1))
    assert asyncio.run(api_tokens.verify_api_token(_credentials(), db)) is None
    assert db.events == ["rollback"]
