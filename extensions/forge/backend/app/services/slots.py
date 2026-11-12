# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The windows of a node's slots, in time.

A slot is a node and a recurring window: the days of the week it starts on,
a start and an end as HH:MM, in a time zone. A window belongs to the day it
starts on; an end at or before the start is on the next day, so "23:00" to
"07:00" crosses midnight, and an end equal to the start is a whole day. The
local times are read in the slot's time zone on each day, so a window keeps
its wall-clock hours across a change of summer time.

The windows of all the slots of a node are merged where they overlap or
touch: two whole days in a row are one window of 48 hours, and a training
does not stop between them.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Madrid"
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# How far around a moment the windows are laid out: a window starts at most a
# day before the moment it contains, and the next one within a week
LOOK_BACK_DAYS = 2
LOOK_AHEAD_DAYS = 8


class SlotError(ValueError):
    """A slot's days, times or time zone are not valid; the message says which."""


@dataclass(frozen=True)
class Window:
    """One window of a node, in UTC, with the slots it came from."""

    node: str
    start: datetime
    end: datetime
    slot_ids: tuple

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def to_dict(self):
        return {"node": self.node, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "slot_ids": list(self.slot_ids)}


def clock_time(value: str, what: str) -> time:
    """A time of day written HH:MM."""
    try:
        hours, minutes = value.split(":")
        if len(hours) != 2 or len(minutes) != 2:
            raise ValueError
        return time(int(hours), int(minutes))
    except ValueError:
        raise SlotError(f"{what} must be a time of day written HH:MM, such as 23:00, not {value!r}")


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SlotError(f"{name!r} is not a time zone of the IANA database, such as {DEFAULT_TIMEZONE}")


def check(days: Iterable[int], start: str, end: str, timezone_name: str) -> None:
    """Refuse a slot that cannot be laid out in time."""
    days = list(days)
    if not days:
        raise SlotError("a slot needs at least one day of the week")
    wrong = [d for d in days if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6]
    if wrong:
        raise SlotError(f"days are numbers from 0 (Monday) to 6 (Sunday), not {wrong}")
    if len(set(days)) != len(days):
        raise SlotError(f"days names a day twice: {days}")
    clock_time(start, "start")
    clock_time(end, "end")
    zone(timezone_name)


def occurrence(day: date, start: str, end: str, timezone_name: str):
    """The window a slot opens on `day` (its local date), as (start, end) in UTC."""
    tz = zone(timezone_name)
    begins, ends = clock_time(start, "start"), clock_time(end, "end")
    end_day = day if ends > begins else day + timedelta(days=1)
    return (datetime.combine(day, begins, tzinfo=tz).astimezone(timezone.utc),
            datetime.combine(end_day, ends, tzinfo=tz).astimezone(timezone.utc))


def windows(slots: Iterable, moment: datetime) -> List[Window]:
    """The merged windows of the slots of one node around `moment`, in order.

    Each slot needs node, days, start, end and timezone, as the Slot model has
    them; all slots given must be of the same node."""
    slots = list(slots)
    if not slots:
        return []
    nodes = {s.node for s in slots}
    if len(nodes) != 1:
        raise ValueError(f"windows are laid out for one node at a time, not {sorted(nodes)}")
    raw = []
    for s in slots:
        local = moment.astimezone(zone(s.timezone)).date()
        for offset in range(-LOOK_BACK_DAYS, LOOK_AHEAD_DAYS + 1):
            day = local + timedelta(days=offset)
            if day.weekday() in s.days:
                begins, ends = occurrence(day, s.start, s.end, s.timezone)
                raw.append((begins, ends, s.id))
    raw.sort()
    merged: List[Window] = []
    for begins, ends, slot_id in raw:
        if merged and begins <= merged[-1].end:
            last = merged[-1]
            ids = last.slot_ids if slot_id in last.slot_ids else last.slot_ids + (slot_id,)
            merged[-1] = Window(last.node, last.start, max(last.end, ends), ids)
        else:
            merged.append(Window(slots[0].node, begins, ends, (slot_id,)))
    return merged


def active(slots: Iterable, moment: datetime) -> Optional[Window]:
    """The window of the node that contains `moment`, or None."""
    return next((w for w in windows(slots, moment) if w.contains(moment)), None)


def upcoming(slots: Iterable, moment: datetime) -> Optional[Window]:
    """The node's next window that starts after `moment`, or None when it has no slot."""
    return next((w for w in windows(slots, moment) if w.start > moment), None)


def overlapping(first: Iterable, second: Iterable, moment: datetime) -> List[tuple]:
    """The pairs of windows of two nodes that overlap in the week after `moment`."""
    horizon = moment + timedelta(days=7)
    found = []
    for a in windows(first, moment):
        for b in windows(second, moment):
            if a.start < b.end and b.start < a.end and a.end > moment and a.start < horizon:
                found.append((a, b))
    return found
