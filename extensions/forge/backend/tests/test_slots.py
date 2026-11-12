# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The windows of a node's slots: the days they start on, windows that cross
midnight, the slot's time zone and its summer time, and windows that touch."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.forge import Slot
from app.services import slots

MADRID = ZoneInfo("Europe/Madrid")
MONDAY, FRIDAY, SATURDAY, SUNDAY = 0, 4, 5, 6


def slot(days, start, end, tz="Europe/Madrid", node="tkspark", id="s1"):
    return Slot(id=id, node=node, days=days, start=start, end=end, timezone=tz)


def madrid(*args):
    return datetime(*args, tzinfo=MADRID).astimezone(timezone.utc)


def test_a_window_is_open_from_its_start_to_its_end_on_its_days():
    office = [slot([MONDAY], "09:00", "17:00")]
    # 2 November 2026 is a Monday
    assert slots.active(office, madrid(2026, 11, 2, 10, 0)) is not None
    assert slots.active(office, madrid(2026, 11, 2, 9, 0)) is not None
    assert slots.active(office, madrid(2026, 11, 2, 17, 0)) is None
    assert slots.active(office, madrid(2026, 11, 3, 10, 0)) is None


def test_a_window_that_crosses_midnight_belongs_to_the_day_it_starts():
    night = [slot([FRIDAY], "23:00", "07:00")]
    # Friday 6 November 2026 at 23:00 to Saturday 7 at 07:00
    window = slots.active(night, madrid(2026, 11, 7, 3, 0))
    assert window is not None
    assert (window.start, window.end) == (madrid(2026, 11, 6, 23, 0), madrid(2026, 11, 7, 7, 0))
    # Thursday night is not a Friday window
    assert slots.active(night, madrid(2026, 11, 5, 23, 30)) is None
    assert slots.active(night, madrid(2026, 11, 6, 7, 30)) is None


def test_the_hours_are_the_slots_own_time_zone_in_summer_and_in_winter():
    night = [slot(list(range(7)), "23:00", "07:00")]
    summer = slots.active(night, madrid(2026, 7, 1, 23, 30))
    winter = slots.active(night, madrid(2026, 1, 14, 23, 30))
    assert summer.start == datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)
    assert winter.start == datetime(2026, 1, 14, 22, 0, tzinfo=timezone.utc)
    new_york = [slot(list(range(7)), "23:00", "07:00", tz="America/New_York")]
    assert slots.active(new_york, datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc)).start == \
        datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc)


def test_a_night_when_the_clocks_go_back_is_an_hour_longer():
    # Summer time ends in Madrid at 03:00 on Sunday 25 October 2026
    night = [slot([SATURDAY], "23:00", "07:00")]
    window = slots.active(night, madrid(2026, 10, 25, 1, 0))
    assert window.end - window.start == timedelta(hours=9)


def test_an_end_equal_to_the_start_is_a_whole_day_and_touching_days_are_one_window():
    weekend = [slot([SATURDAY, SUNDAY], "00:00", "00:00")]
    window = slots.active(weekend, madrid(2026, 11, 7, 12, 0))
    assert (window.start, window.end) == (madrid(2026, 11, 7, 0, 0), madrid(2026, 11, 9, 0, 0))
    assert slots.active(weekend, madrid(2026, 11, 9, 0, 0)) is None


def test_two_slots_of_a_node_that_overlap_are_one_window():
    both = [slot([FRIDAY], "20:00", "02:00", id="a"), slot([SATURDAY], "00:00", "08:00", id="b")]
    window = slots.active(both, madrid(2026, 11, 7, 5, 0))
    assert (window.start, window.end) == (madrid(2026, 11, 6, 20, 0), madrid(2026, 11, 7, 8, 0))
    assert set(window.slot_ids) == {"a", "b"}


def test_the_next_window_is_the_first_that_starts_later():
    night = [slot([FRIDAY], "23:00", "07:00")]
    nxt = slots.upcoming(night, madrid(2026, 11, 2, 12, 0))
    assert nxt.start == madrid(2026, 11, 6, 23, 0)
    assert slots.upcoming([], madrid(2026, 11, 2, 12, 0)) is None


def test_windows_of_two_nodes_that_overlap_are_found():
    spark = [slot([FRIDAY], "22:00", "07:00", node="tkspark", id="a")]
    gpu = [slot([FRIDAY], "23:00", "09:00", node="tkgpu", id="b")]
    found = slots.overlapping(spark, gpu, madrid(2026, 11, 2, 12, 0))
    assert len(found) == 1
    a, b = found[0]
    assert (max(a.start, b.start), min(a.end, b.end)) == (madrid(2026, 11, 6, 23, 0), madrid(2026, 11, 7, 7, 0))
    office = [slot([MONDAY], "09:00", "17:00", node="tkgpu", id="c")]
    assert slots.overlapping(spark, office, madrid(2026, 11, 2, 12, 0)) == []


@pytest.mark.parametrize("days,start,end,tz,message", [
    ([], "23:00", "07:00", "Europe/Madrid", "at least one day"),
    ([7], "23:00", "07:00", "Europe/Madrid", "0 \\(Monday\\) to 6"),
    ([1, 1], "23:00", "07:00", "Europe/Madrid", "twice"),
    ([1], "25:00", "07:00", "Europe/Madrid", "start must be"),
    ([1], "23:00", "7", "Europe/Madrid", "end must be"),
    ([1], "23:00", "07:00", "Mars/Olympus", "not a time zone"),
])
def test_a_slot_that_cannot_be_laid_out_is_refused(days, start, end, tz, message):
    with pytest.raises(slots.SlotError, match=message):
        slots.check(days, start, end, tz)
