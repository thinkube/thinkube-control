# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""How a run reports: messages on NATS, kept by a JetStream stream.

A run publishes on <subjects>.runs.<run id>.<kind>:

  started   what was run: the task, its inputs, outputs and settings, the
            commit, the host. Once.
  status    the state, the stages with their progress, the counts and the
            measures. The whole status each time, a few times a second
            while it changes.
  samples   the samples the run keeps: the latest ones, and ones spaced
            across the run. The whole set each time.
  failure   one item the task reported as failed.
  log       a piece of what the run printed.
  alive     a heartbeat, every few seconds.

The Mill control center creates the stream, reads it, and keeps each run's
record. It asks a run to stop with a request on
<subjects>.control.<run id>.stop, which the run answers.

<subjects> is "mill" unless MILL_SUBJECTS names another prefix. The server
is the platform's NATS, at the address its playbook gives it
(ansible/40_thinkube/optional/nats/10_deploy.yaml of thinkube), unless
MILL_NATS_URL names another.
"""

import asyncio
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

FORMAT = "thinkube-mill/2"

URL_VARIABLE = "MILL_NATS_URL"
PLATFORM_URL = "nats://nats.nats.svc.cluster.local:4222"

SUBJECTS_VARIABLE = "MILL_SUBJECTS"
PLATFORM_SUBJECTS = "mill"

# Seconds one attempt to connect or to publish may take
ATTEMPT_TIMEOUT = 5.0

# Seconds a message is tried again for while the server cannot be reached,
# so a restart of NATS does not end the runs in progress
RETRY_FOR = 60.0

# Seconds between two heartbeats
ALIVE_EVERY = 10.0

RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ReportError(Exception):
    """A run's message could not be sent; the message says why."""


def nats_url() -> str:
    return os.environ.get(URL_VARIABLE) or PLATFORM_URL


def subjects() -> str:
    return os.environ.get(SUBJECTS_VARIABLE) or PLATFORM_SUBJECTS


def stream_name(prefix: str) -> str:
    """The stream that keeps the messages of a subjects prefix: 'mill' is MILL."""
    return re.sub(r"[^A-Za-z0-9]", "_", prefix).upper()


class Reporter:
    """A run's connection to NATS. It runs on a thread of its own, so a task needs no asyncio."""

    def __init__(self, run_id: str, on_stop: Optional[Callable[[], None]] = None) -> None:
        if not RUN_ID.match(run_id):
            raise ReportError(f"the run id {run_id!r} may hold only letters, digits, '-' and '_'")
        self.run_id = run_id
        self.url = nats_url()
        self.prefix = subjects()
        self.stream = stream_name(self.prefix)
        self._on_stop = on_stop
        self._nc: Any = None
        self._js: Any = None
        self._alive_task: Any = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mill-report")
        self._thread.start()
        try:
            self._do(self._connect())
        except ReportError:
            self._stop_loop()
            raise
        except Exception as e:
            self._stop_loop()
            why = f"no answer within {int(ATTEMPT_TIMEOUT * 3)} seconds" if isinstance(e, TimeoutError) else f"{type(e).__name__}: {e}"
            raise ReportError(
                f"cannot connect to NATS at {self.url}: {why}. "
                f"A run reports to the Mill control center through it; {URL_VARIABLE} names another address."
            ) from e

    def _do(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(ATTEMPT_TIMEOUT * 3)

    def _stop_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _connect(self) -> None:
        import nats
        from nats.js.errors import NotFoundError

        self._nc = await nats.connect(
            self.url,
            name=f"mill-run-{self.run_id}",
            connect_timeout=ATTEMPT_TIMEOUT,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
            error_cb=self._connection_error,
        )
        self._js = self._nc.jetstream()
        try:
            await self._js.stream_info(self.stream)
        except NotFoundError:
            await self._nc.close()
            raise ReportError(
                f"NATS at {self.url} has no stream {self.stream}. The Mill control center creates it "
                "when it starts: check that the Mill extension of Thinkube Control is deployed and running."
            )
        if self._on_stop is not None:
            await self._nc.subscribe(f"{self.prefix}.control.{self.run_id}.stop", cb=self._stop_asked)
        self._alive_task = self._loop.create_task(self._alive())

    async def _connection_error(self, error: Exception) -> None:
        # The client connects again by itself; a message that cannot be sent says so in send()
        pass

    async def _stop_asked(self, message: Any) -> None:
        await message.respond(b'{"stopping": true}')
        if self._on_stop is not None:
            self._on_stop()

    async def _alive(self) -> None:
        subject = f"{self.prefix}.runs.{self.run_id}.alive"
        body = json.dumps({"format": FORMAT, "run": self.run_id}).encode()
        while True:
            await asyncio.sleep(ALIVE_EVERY)
            try:
                await self._nc.publish(subject, body)
            except Exception:
                # A heartbeat that cannot be sent is not sent: its absence is what tells
                pass

    def send(self, kind: str, payload: Dict[str, Any]) -> None:
        """Publish a message and wait until the stream has it. Tried again while NATS cannot be reached."""
        body = json.dumps({"format": FORMAT, "run": self.run_id, **payload}, ensure_ascii=False, default=str)
        subject = f"{self.prefix}.runs.{self.run_id}.{kind}"
        deadline = time.monotonic() + RETRY_FOR
        while True:
            try:
                self._do(self._js.publish(subject, body.encode("utf-8"), timeout=ATTEMPT_TIMEOUT))
                return
            except Exception as e:
                last = e
            if time.monotonic() >= deadline:
                raise ReportError(
                    f"the run's {kind} could not be sent to NATS at {self.url} for {int(RETRY_FOR)} seconds: "
                    f"{type(last).__name__}: {last}"
                )
            time.sleep(1)

    async def _close(self) -> None:
        if self._alive_task is not None:
            self._alive_task.cancel()
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.drain()

    def close(self) -> None:
        try:
            self._do(self._close())
        finally:
            self._stop_loop()
