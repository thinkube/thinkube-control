# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The control center's connection to NATS.

The runs publish what they do on <subjects>.runs.<run id>.<kind> (the
library's _report says which kinds). The stream named after the subjects
keeps those messages; this module creates it, reads it with a durable
consumer, and hands each message to the store, which keeps the runs'
records in the database. A message is acknowledged once it is stored, so a
restart of this pod loses none.

It also asks a run to stop: a request on <subjects>.control.<run id>.stop,
which the run's process answers, wherever it executes.
"""

import asyncio
import json
import logging
import re
from typing import Any, List, Optional, Tuple

import nats
from nats.js.errors import NotFoundError

from app.core.config import settings
from app.db.session import get_session_local
from app.services import store

log = logging.getLogger(__name__)

CONSUMER = "control-center"

# What the stream keeps: a message is needed only until it is stored here,
# and two days cover a long stop of the control center
STREAM_MAX_AGE = 2 * 24 * 3600
STREAM_MAX_BYTES = 1024 ** 3

BATCH = 200
FETCH_WAIT = 1.0

# Seconds between two tries after a failure: of the connection, the stream or the database
RETRY_EVERY = 3.0

# Seconds a run has to answer a request to stop
STOP_WAIT = 5.0


def stream_name(prefix: str) -> str:
    """The stream that keeps the messages of a subjects prefix: 'mill' is MILL. As the library names it."""
    return re.sub(r"[^A-Za-z0-9]", "_", prefix).upper()


class NotConnected(Exception):
    """The control center has no connection to NATS; the message says why."""


class Consumer:
    def __init__(self) -> None:
        self.prefix = settings.MILL_SUBJECTS
        self.stream = stream_name(self.prefix)
        self._nc: Any = None
        self._task: Optional[asyncio.Task] = None
        # Why no message is being read, when none is; None while reading
        self.problem: Optional[str] = f"the control center is connecting to NATS at {settings.MILL_NATS_URL}"
        # Whether every message of the stream is stored
        self.caught_up = False

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.close()

    async def _connection_error(self, error: Exception) -> None:
        # The client connects again by itself; _run says what is wrong while no message is read
        pass

    async def _subscribe(self) -> Any:
        if self._nc is None or self._nc.is_closed:
            self._nc = await nats.connect(
                settings.MILL_NATS_URL, name="mill-control-center", connect_timeout=5,
                max_reconnect_attempts=-1, reconnect_time_wait=1, error_cb=self._connection_error,
            )
        js = self._nc.jetstream()
        try:
            await js.stream_info(self.stream)
        except NotFoundError:
            await js.add_stream(
                name=self.stream, subjects=[f"{self.prefix}.runs.>"],
                max_age=STREAM_MAX_AGE, max_bytes=STREAM_MAX_BYTES,
            )
        return await js.pull_subscribe(f"{self.prefix}.runs.>", durable=CONSUMER, stream=self.stream)

    async def _run(self) -> None:
        subscription = None
        while True:
            try:
                if subscription is None:
                    subscription = await asyncio.wait_for(self._subscribe(), timeout=20)
                try:
                    messages = await subscription.fetch(batch=BATCH, timeout=FETCH_WAIT)
                except nats.errors.TimeoutError:
                    messages = []
                self.problem = None
                if messages:
                    await asyncio.to_thread(_store, [_parsed(m) for m in messages])
                    for message in messages:
                        await message.ack()
                    self.caught_up = messages[-1].metadata.num_pending == 0
                else:
                    self.caught_up = True
                    await asyncio.to_thread(_mark_lost)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                why = "no answer in 20 seconds" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
                self.problem = (
                    f"the control center cannot read the runs' messages from NATS at {settings.MILL_NATS_URL} "
                    f"(stream {self.stream}): {why}"
                )
                self.caught_up = False
                log.error(self.problem)
                subscription = None
                await asyncio.sleep(RETRY_EVERY)

    async def request_stop(self, run_id: str) -> bool:
        """Ask a run to stop. True when its process answered, False when no process listens."""
        if self._nc is None or not self._nc.is_connected:
            raise NotConnected(self.problem or f"the control center is not connected to NATS at {settings.MILL_NATS_URL}")
        try:
            await self._nc.request(f"{self.prefix}.control.{run_id}.stop", b"", timeout=STOP_WAIT)
        except (nats.errors.NoRespondersError, nats.errors.TimeoutError):
            return False
        return True


def _parsed(message: Any) -> Tuple[str, dict, Any]:
    """A message as the store takes it: its kind, its content, and when the stream stored it."""
    kind = message.subject.rsplit(".", 1)[-1]
    return kind, json.loads(message.data), message.metadata.timestamp


def _store(messages: List[Tuple[str, dict, Any]]) -> None:
    db = get_session_local()()
    try:
        for kind, payload, stored_at in messages:
            store.apply(db, kind, payload, stored_at)
        db.commit()
    finally:
        db.close()


def _mark_lost() -> None:
    db = get_session_local()()
    try:
        if store.mark_lost(db):
            db.commit()
    finally:
        db.close()


# The control center's one consumer, started with the app
consumer: Optional[Consumer] = None
