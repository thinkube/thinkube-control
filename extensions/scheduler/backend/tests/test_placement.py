# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Where a job runs: the GPUs of its node it is given, or what it waits for."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.jobs import RUNNING, WAITING, Job
from app.services import placement

GIB = 2**30
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def job(name, gpus=1, gpu_memory_gb=None, node="tkamd2", status=WAITING, gpu_ids=None, memory_gb=8, started=None):
    return Job(id=name, name=name, node=node, gpus=gpus, gpu_memory_gb=gpu_memory_gb, memory_gb=memory_gb,
               status=status, gpu_ids=gpu_ids, started_at=started)


def cards(used=(0.0, 0.0)):
    return {"name": "tkamd2", "kind": "separate", "memory_available_bytes": 100 * GIB,
            "gpus": [{"id": f"GPU-{i}", "index": i, "memory_total_bytes": 24 * GIB,
                      "memory_used_bytes": int(u * GIB)} for i, u in enumerate(used)]}


def spark(available=100):
    return {"name": "tkspark", "kind": "shared", "memory_available_bytes": available * GIB,
            "gpus": [{"id": "GPU-s", "index": 0}]}


def test_a_job_without_a_gpu_is_given_none_and_waits_for_nothing():
    assert placement.place(job("a", gpus=0), cards(), [], NOW) == ([], None)


def test_jobs_alone_take_one_card_each_and_the_third_waits():
    first, second, third = job("a"), job("b"), job("c")
    jobs = [first, second, third]
    assert placement.place(first, cards(), jobs, NOW) == (["GPU-0"], None)
    first.status, first.gpu_ids = RUNNING, ["GPU-0"]
    assert placement.place(second, cards(), jobs, NOW) == (["GPU-1"], None)
    second.status, second.gpu_ids = RUNNING, ["GPU-1"]
    given, why = placement.place(third, cards(), jobs, NOW)
    assert given is None
    assert why == "waiting for a GPU of tkamd2 (a GPU alone): GPU 0 holds job a; GPU 1 holds job b"


def test_a_job_on_both_cards_needs_both_free():
    both = job("both", gpus=2)
    assert placement.place(both, cards(), [both], NOW) == (["GPU-0", "GPU-1"], None)
    given, why = placement.place(both, cards(used=(22.0, 0.0)), [both], NOW)
    assert given is None and "GPU 0 holds 22.0 GiB of work the scheduler did not start" in why


def test_small_jobs_share_a_card_while_their_memory_fits():
    # Five jobs of 4 GiB on one card of 24 GiB, 5% of it kept free: 22.8 GiB hold five, not six
    running = [job(f"r{i}", gpu_memory_gb=4, status=RUNNING, gpu_ids=["GPU-0"]) for i in range(5)]
    sixth = job("sixth", gpu_memory_gb=4)
    used = cards(used=(20.0, 0.0))
    # The fullest card that holds it comes first; card 0 has 2.8 GiB left, so the sixth goes to card 1
    assert placement.place(sixth, used, running + [sixth], NOW) == (["GPU-1"], None)
    small = job("small", gpu_memory_gb=2)
    assert placement.place(small, used, running + [small], NOW) == (["GPU-0"], None)


def test_work_the_scheduler_did_not_start_counts_by_what_the_card_reports():
    # A served model holds 15 GiB of card 0; the scheduler gave nothing there
    eight = job("eight", gpu_memory_gb=8)
    assert placement.place(eight, cards(used=(15.0, 15.0)), [eight], NOW)[1] == (
        "waiting for a GPU of tkamd2 (8.0 GiB on a GPU): GPU 0 has 7.8 GiB free; GPU 1 has 7.8 GiB free")
    seven = job("seven", gpu_memory_gb=7)
    assert placement.place(seven, cards(used=(15.0, 15.0)), [seven], NOW) == (["GPU-0"], None)
    # A job of the scheduler's that has not loaded yet still holds what it was given
    loading = job("loading", gpu_memory_gb=20, status=RUNNING, gpu_ids=["GPU-0"])
    assert placement.place(seven, cards(used=(0.0, 0.0)), [loading, seven], NOW) == (["GPU-1"], None)


def test_a_job_alone_does_not_share_and_is_not_joined():
    alone = job("alone", status=RUNNING, gpu_ids=["GPU-0"])
    small = job("small", gpu_memory_gb=2)
    assert placement.place(small, cards(used=(1.0, 0.0)), [alone, small], NOW) == (["GPU-1"], None)
    other = job("other", status=RUNNING, gpu_ids=["GPU-1"], gpu_memory_gb=2)
    wants_alone = job("wants")
    given, why = placement.place(wants_alone, cards(used=(1.0, 2.0)), [alone, other, wants_alone], NOW)
    assert given is None and "GPU 0 is job alone's alone; GPU 1 holds job other" in why


def test_a_job_that_can_never_run_on_its_node_is_refused():
    with pytest.raises(placement.CannotRun, match="tkamd2 has 2 GPUs, and the job asks for 3"):
        placement.place(job("a", gpus=3), cards(), [], NOW)
    with pytest.raises(placement.CannotRun, match="takes 30.0 GiB on a GPU"):
        placement.place(job("a", gpu_memory_gb=30), cards(), [], NOW)
    with pytest.raises(placement.CannotRun, match="tkspark has one GPU"):
        placement.place(job("a", gpus=2, node="tkspark"), spark(), [], NOW)
    with pytest.raises(placement.CannotRun, match="tkamd1 has no GPU"):
        placement.place(job("a", node="tkamd1"), {"name": "tkamd1", "kind": "none", "gpus": []}, [], NOW)


def test_on_a_shared_node_a_job_fits_by_its_memory_and_its_gpu_memory_together():
    a = job("a", node="tkspark", gpu_memory_gb=40, memory_gb=20)
    assert placement.place(a, spark(available=70), [a], NOW) == (["GPU-s"], None)
    given, why = placement.place(a, spark(available=50), [a], NOW)
    assert given is None and why == ("waiting for 60.0 GiB of memory on tkspark, whose GPU takes its memory from the "
                                     "system's: 50.0 GiB is available")
    # A job started a minute ago has not loaded: what it was given is not in the node's figure yet
    started = job("started", node="tkspark", gpu_memory_gb=30, memory_gb=10, status=RUNNING, gpu_ids=["GPU-s"],
                  started=NOW - timedelta(minutes=1))
    assert placement.place(a, spark(available=90), [started, a], NOW)[0] is None
    started.started_at = NOW - timedelta(minutes=30)
    assert placement.place(a, spark(available=90), [started, a], NOW) == (["GPU-s"], None)


def test_on_a_shared_node_a_job_alone_waits_for_the_other_gpu_jobs():
    other = job("other", node="tkspark", gpu_memory_gb=10, status=RUNNING, gpu_ids=["GPU-s"])
    alone = job("alone", node="tkspark")
    assert placement.place(alone, spark(), [other, alone], NOW) == (
        None, "waiting for the GPU of tkspark alone: it holds job other")
    assert placement.place(alone, spark(), [alone], NOW) == (["GPU-s"], None)
    sharing = job("sharing", node="tkspark", gpu_memory_gb=10)
    alone.status, alone.gpu_ids = RUNNING, ["GPU-s"]
    assert placement.place(sharing, spark(), [alone, sharing], NOW) == (
        None, "waiting for the GPU of tkspark: it is job alone's alone")


def test_a_node_that_could_not_be_read_makes_its_gpu_jobs_wait():
    assert placement.place(job("a"), {"name": "tkamd2", "error": "no answer"}, [], NOW) == (
        None, "waiting for tkamd2 to be read: no answer")
