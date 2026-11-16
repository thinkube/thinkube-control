# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The state of a run, and the functions a task reports with.

A run sends what it knows as messages, through the function it is given:

  started   what was run. Once.
  status    the state, the stages with their progress, the counts and the
            measures. The whole status each time it changed, at most a few
            times a second.
  samples   the samples the run keeps: the latest ones, and ones spaced
            across the run. The whole set, with the status, when it changed.
  failure   one item the task reported as failed.

_report says where the messages go.
"""

import collections
import datetime
import json
import time
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Optional

# Seconds between two status messages
WRITE_INTERVAL = 0.5

# How many samples a record keeps: the latest, and ones spaced across the run
LATEST_KEPT = 10
SPACED_KEPT = 20

# How many failed items are sent; the status counts them all
FAILURES_KEPT = 200

# How many stages the status keeps; the oldest finished ones leave first
STAGES_KEPT = 30

# The longest text a sample keeps of one value; the rest is cut and counted
SAMPLE_TEXT_LIMIT = 2000

# The longest a whole sample may be as JSON text. A samples message holds
# up to 30 samples and NATS takes messages up to 1 MB.
SAMPLE_SIZE_LIMIT = 20_000

Send = Callable[[str, Dict[str, Any]], None]


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _shorten(value: Any, depth: int = 0) -> Any:
    """A value as a sample keeps it: long text cut, with what was cut counted."""
    if isinstance(value, str):
        if len(value) > SAMPLE_TEXT_LIMIT:
            return value[:SAMPLE_TEXT_LIMIT] + f"… [{len(value) - SAMPLE_TEXT_LIMIT} more characters]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= 6:
        return _shorten(repr(value), depth)
    if isinstance(value, dict):
        return {str(k): _shorten(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        items = [_shorten(v, depth + 1) for v in value[:50]]
        if len(value) > 50:
            items.append(f"… [{len(value) - 50} more items]")
        return items
    return _shorten(repr(value), depth)


def _fit(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The fields of a sample, shortened; a sample still too long is kept as the start of its JSON text."""
    short = _shorten(fields)
    text = json.dumps(short, ensure_ascii=False, default=str)
    if len(text) <= SAMPLE_SIZE_LIMIT:
        return short
    return {"sample": text[:SAMPLE_SIZE_LIMIT] + f"… [{len(text) - SAMPLE_SIZE_LIMIT} more characters]"}


class Stage:
    """One named piece of a run's work, with how much of it is done."""

    def __init__(self, run: "Run", name: str, total: Optional[int]) -> None:
        self._run = run
        self.name = name
        self.total = total
        self.done = 0
        self.started_at = now()
        self.ended_at: Optional[str] = None

    def advance(self, n: int = 1) -> None:
        self.done += n
        self._run.changed()

    def set(self, done: int, total: Optional[int] = None) -> None:
        self.done = done
        if total is not None:
            self.total = total
        self._run.changed()

    def end(self) -> None:
        if self.ended_at is None:
            self.ended_at = now()
            self._run.changed(at_once=True)

    def record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total": self.total,
            "done": self.done,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class Run:
    """One run of a task: what it knows, and the sending of it."""

    def __init__(self, info: Dict[str, Any], send: Send) -> None:
        self.id = info["id"]
        self._send = send
        self.state = "running"
        self.error: Optional[str] = None
        self.started_at = now()
        self.ended_at: Optional[str] = None
        self.stages: List[Stage] = []
        self.counters: Dict[str, float] = {}
        self.measures: Dict[str, Dict[str, float]] = {}
        self.shown = 0
        self.failures = 0
        self.explicit_samples = False
        self.last_input: Any = None
        self._latest: Deque[Dict[str, Any]] = collections.deque(maxlen=LATEST_KEPT)
        self._spaced: List[Dict[str, Any]] = []
        self._spacing = 1
        self._seq = 0
        self._samples_changed = False
        self._written_at = 0.0
        self._send("started", {**info, "started_at": self.started_at})
        self.write()

    # -- stages ----------------------------------------------------------

    def stage(self, name: str, total: Optional[int] = None) -> Stage:
        """A new stage. A name already in use gets a number: 'labelling (2)'."""
        names = {s.name for s in self.stages}
        unique, n = name, 1
        while unique in names:
            n += 1
            unique = f"{name} ({n})"
        stage = Stage(self, unique, total)
        self.stages.append(stage)
        while len(self.stages) > STAGES_KEPT:
            finished = next((s for s in self.stages if s.ended_at is not None), None)
            self.stages.remove(finished if finished is not None else self.stages[0])
        self.changed(at_once=True)
        return stage

    # -- totals ----------------------------------------------------------

    def count(self, name: str, n: float = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n
        self.changed()

    def measure(self, name: str, value: float) -> None:
        m = self.measures.get(name)
        if m is None:
            self.measures[name] = {"count": 1, "sum": value, "min": value, "max": value}
        else:
            m["count"] += 1
            m["sum"] += value
            m["min"] = min(m["min"], value)
            m["max"] = max(m["max"], value)
        self.changed()

    # -- samples and failures ----------------------------------------------

    def sample(self, fields: Dict[str, Any]) -> None:
        """Keep a sample: always among the latest, and among the spaced ones every so often."""
        self.shown += 1
        entry = {"n": self.shown, "at": now(), "fields": _fit(fields)}
        self._latest.append(entry)
        if (self.shown - 1) % self._spacing == 0:
            self._spaced.append(entry)
            if len(self._spaced) > SPACED_KEPT:
                # Twice the distance between kept samples, so they still span the run
                self._spaced = self._spaced[::2]
                self._spacing *= 2
        self._samples_changed = True
        self.changed()

    def failure(self, error: str, fields: Dict[str, Any]) -> None:
        self.failures += 1
        if self.failures <= FAILURES_KEPT:
            self._send("failure", {"n": self.failures, "at": now(), "error": error, "fields": _fit(fields)})
        self.changed(at_once=True)

    # -- sending ---------------------------------------------------------

    def changed(self, at_once: bool = False) -> None:
        if at_once or time.monotonic() - self._written_at >= WRITE_INTERVAL:
            self.write()

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "error": self.error,
            "started_at": self.started_at,
            "updated_at": now(),
            "ended_at": self.ended_at,
            "stages": [s.record() for s in self.stages],
            "counters": dict(self.counters),
            "measures": {k: dict(v) for k, v in self.measures.items()},
            "samples": self.shown,
            "failures": self.failures,
        }

    def write(self) -> None:
        """Send the status, and the samples when they changed. seq orders a run's messages."""
        self._seq += 1
        self._send("status", {"seq": self._seq, **self.status()})
        if self._samples_changed:
            self._send("samples", {"seq": self._seq, "latest": list(self._latest), "spaced": list(self._spaced)})
            self._samples_changed = False
        self._written_at = time.monotonic()

    def finish(self, state: str, error: Optional[str] = None) -> None:
        """End the run as completed, failed or stopped. Open stages end with it."""
        self.state = state
        self.error = error
        self.ended_at = now()
        for stage in self.stages:
            if stage.ended_at is None:
                stage.ended_at = self.ended_at
        self.write()


# The run the current process reports to; None when a task's function is
# called as plain Python
_current: Optional[Run] = None


def current() -> Optional[Run]:
    return _current


def set_current(run: Optional[Run]) -> None:
    global _current
    _current = run


def track(items: Iterable[Any], name: str, total: Optional[int] = None) -> Iterator[Any]:
    """Go through ``items`` and report how far the run is, as the stage ``name``.

    ``total`` is the number of items when ``items`` cannot say it itself.
    """
    run = _current
    if run is None:
        yield from items
        return
    if total is None:
        try:
            total = len(items)  # type: ignore[arg-type]
        except TypeError:
            total = None
    stage = run.stage(name, total)
    try:
        for item in items:
            run.last_input = item
            yield item
            stage.advance()
    finally:
        stage.end()


def show(**fields: Any) -> None:
    """Send a sample to the control center: ``show(input=row, output=cleaned)``."""
    run = _current
    if run is None:
        return
    run.explicit_samples = True
    run.sample(fields)


def count(name: str, n: float = 1) -> None:
    """Add to a running count: ``count("dropped: too short")``."""
    if _current is not None:
        _current.count(name, n)


def measure(name: str, value: float) -> None:
    """Add a value to a running measure, which keeps its count, mean, least and most."""
    if _current is not None:
        _current.measure(name, value)


def fail(error: str, **fields: Any) -> None:
    """Record an item that went wrong, and go on: ``fail("no answer", input=row)``.

    The run does not stop. A task that must stop raises an exception.
    """
    if _current is not None:
        _current.failure(error, fields)
