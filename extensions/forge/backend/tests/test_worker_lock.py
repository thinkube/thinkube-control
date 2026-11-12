# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The queue worker's advisory lock is released after every tick, so the next tick of the same pod takes it."""

import asyncio

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.services import worker


def held_locks(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :k "
                                       "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"),
                                  {"k": worker.LOCK_KEY & 0xFFFFFFFF}).scalar()


def test_every_tick_takes_the_lock_and_gives_it_back(db_engine, monkeypatch):
    factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(worker, "credential", lambda db: None)
    for _ in range(5):
        asyncio.run(worker.tick_once(factory))
        # No Control token: the tick got the lock and stopped there, never "another pod"
        assert worker.state["error"] == "the queue has no Control token; connect it on Forge's Slots page"
        assert held_locks(db_engine) == 0


def test_a_lock_held_elsewhere_makes_the_tick_wait(db_engine, monkeypatch):
    factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(worker, "credential", lambda db: None)
    with db_engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": worker.LOCK_KEY}).scalar()
        asyncio.run(worker.tick_once(factory))
        assert worker.state["error"] == "another Forge pod's worker holds the queue"
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": worker.LOCK_KEY})
    asyncio.run(worker.tick_once(factory))
    assert worker.state["error"] == "the queue has no Control token; connect it on Forge's Slots page"
