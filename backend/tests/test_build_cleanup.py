# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Old builds go; the newest, the recent, the unfinished and the one running are kept."""

from datetime import datetime, timedelta, timezone

from app.services import build_cleanup as bc

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def builds(app, count, days_apart=1, phase="Succeeded"):
    return [{"name": f"{app}-{i}", "uid": f"uid-{app}-{i}", "app": app,
             "created": NOW - timedelta(days=i * days_apart), "phase": phase} for i in range(count)]


def names(chosen):
    return sorted(b["name"] for b in chosen)


def test_builds_beyond_the_newest_and_older_than_a_week_are_deleted():
    chosen = bc.deletable(builds("forge", 14), {"forge": set()}, NOW)
    # 0..9 are the newest ten; 10..13 are also older than seven days
    assert names(chosen) == ["forge-10", "forge-11", "forge-12", "forge-13"]


def test_recent_builds_beyond_the_newest_ten_are_kept():
    chosen = bc.deletable(builds("control", 30, days_apart=0.2), {"control": set()}, NOW)
    assert names(chosen) == []


def test_the_build_of_the_running_image_is_kept():
    chosen = bc.deletable(builds("forge", 14), {"forge": {"uid-forge-12"}}, NOW)
    assert "forge-12" not in names(chosen)


def test_unfinished_builds_are_kept():
    chosen = bc.deletable(builds("forge", 14, phase="Running"), {"forge": set()}, NOW)
    assert chosen == []


def test_an_app_whose_running_images_are_unknown_keeps_all_its_builds():
    assert bc.deletable(builds("forge", 14), {}, NOW) == []
