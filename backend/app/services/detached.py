"""Work that outlives the request that asked for it.

FastAPI's ``BackgroundTasks`` run inside the same ASGI call as the response.
A client that drives the app in process, as the MCP bridge does through an
ASGI transport, waits for that whole call, so a long task queued that way
holds the MCP tool call open until the task ends. Work that takes minutes is
started here instead: as a task on the event loop that the request does not
wait for.

The task keeps a reference to itself until it ends, so the event loop cannot
drop it half-way, and an exception it raises is logged with its name rather
than lost.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Dict

logger = logging.getLogger(__name__)

_tasks: Dict[str, asyncio.Task] = {}


def start(name: str, work: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Run ``work`` detached from the current request. ``name`` identifies it in the log."""
    task = asyncio.create_task(work, name=name)
    _tasks[name] = task

    def done(finished: asyncio.Task) -> None:
        _tasks.pop(name, None)
        if finished.cancelled():
            logger.warning("detached task %s was cancelled", name)
            return
        error = finished.exception()
        if error is not None:
            logger.error("detached task %s failed: %s", name, error, exc_info=error)

    task.add_done_callback(done)
    return task


def running(name: str) -> bool:
    task = _tasks.get(name)
    return task is not None and not task.done()
