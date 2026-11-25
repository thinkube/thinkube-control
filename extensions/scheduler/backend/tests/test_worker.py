# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The worker: it follows the jobs that run and starts the ones that can."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.jobs import COMPLETED, FAILED, RUNNING, STOPPED, STOPPING, WAITING, Job
from app.services import pods, worker
from app.services.nodes import NodesError

GIB = 2**30
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class FakeNodes:
    def __init__(self):
        self.error = None
        self.used = [0.0, 0.0]

    async def list(self):
        if self.error:
            raise NodesError(self.error)
        return [{"name": "tkamd2", "kind": "separate", "memory_available_bytes": 100 * GIB,
                 "gpus": [{"id": f"GPU-{i}", "index": i, "memory_total_bytes": 24 * GIB,
                           "memory_used_bytes": int(u * GIB)} for i, u in enumerate(self.used)]}]


class FakePods:
    def __init__(self):
        self.started, self.stopped, self.states, self.live, self.refuse = [], [], {}, set(), None

    async def start(self, job, start, gpu_ids):
        if self.refuse:
            raise pods.PodsError(self.refuse)
        name = pods.pod_job_name(job, start)
        self.started.append((name, tuple(gpu_ids)))
        self.states[name] = {"state": "running", "waiting": None}
        self.live.add(name)
        return name

    async def state(self, name):
        return self.states[name]

    async def stop(self, name):
        self.stopped.append(name)

    async def pod_gone(self, name):
        return name not in self.live


@pytest.fixture
def queue(test_db):
    class Queue:
        nodes, pods = FakeNodes(), FakePods()
        count = 0

        def job(self, name, **fields):
            self.count += 1
            base = dict(id=f"{self.count:08d}-{name}", name=name, owner="person", created_by="u", node="tkamd2",
                        image="library/x:1", command=["true"], env={}, mounts=[], gpus=1, memory_gb=4, cpu_cores=1,
                        priority=3, resumable=False, stop_seconds=60, status=WAITING, starts=0,
                        created_at=NOW + timedelta(seconds=self.count))
            made = Job(**{**base, **fields})
            test_db.add(made)
            test_db.flush()
            return made

        def tick(self):
            asyncio.run(worker.Worker(self.nodes, self.pods).tick(test_db, NOW + timedelta(minutes=1)))

    return Queue()


def test_two_jobs_run_on_the_two_cards_and_the_third_starts_when_one_ends(queue):
    a, b, c = queue.job("a"), queue.job("b"), queue.job("c")
    queue.tick()
    assert [(j.status, j.gpu_ids) for j in (a, b)] == [(RUNNING, ["GPU-0"]), (RUNNING, ["GPU-1"])]
    assert (c.status, c.gpu_ids) == (WAITING, None) and "GPU 0 is job a's alone; GPU 1 is job b's alone" in c.waiting
    assert queue.pods.started == [(a.pod_job, ("GPU-0",)), (b.pod_job, ("GPU-1",))]
    queue.pods.states[a.pod_job] = {"state": "completed"}
    queue.tick()
    assert (a.status, a.gpu_ids, a.finished_at is not None) == (COMPLETED, None, True)
    assert (c.status, c.gpu_ids, c.starts, c.waiting) == (RUNNING, ["GPU-0"], 1, None)


def test_the_jobs_of_a_node_start_in_order_and_one_behind_a_waiting_one_waits_behind_it(queue):
    queue.nodes.used = [20.0, 0.0]
    both = queue.job("both", gpus=2, priority=2)
    small = queue.job("small", gpu_memory_gb=2)
    cpu = queue.job("cpu", gpus=0)
    queue.tick()
    # both cannot have card 0, which holds work the scheduler did not start; small would fit on card 1, and waits
    # behind both, so that it does not take the card both is waiting for; a job without a GPU waits for no turn
    assert both.status == WAITING and both.waiting.startswith("waiting for 2 GPUs of tkamd2")
    assert (small.status, small.waiting) == (WAITING, "waiting behind job both, which is ahead of it on tkamd2")
    assert cpu.status == RUNNING
    queue.nodes.used = [0.0, 0.0]
    queue.tick()
    assert (both.status, both.gpu_ids) == (RUNNING, ["GPU-0", "GPU-1"])
    assert small.status == WAITING and "GPU 0 is job both's alone" in small.waiting


def test_a_higher_priority_stops_the_lower_one_on_the_card_it_needs_and_no_other(queue):
    low = queue.job("low", priority=4)
    other = queue.job("other", priority=4)
    queue.tick()
    assert (low.gpu_ids, other.gpu_ids) == (["GPU-0"], ["GPU-1"])
    high = queue.job("high", priority=2)
    queue.tick()
    # One card is enough: the latest started of the two is stopped, the other keeps running
    stopped, kept = (other, low) if other.status == STOPPING else (low, other)
    assert (stopped.status, stopped.preempted_by, kept.status) == (STOPPING, high.id, RUNNING)
    assert high.waiting == f"making room: stopping job {stopped.name} (priority 4)"
    queue.tick()
    # Its pod is still ending: nothing more is stopped
    assert kept.status == RUNNING and high.status == WAITING and queue.pods.stopped == [stopped.pod_job]
    freed = stopped.gpu_ids
    queue.pods.live.discard(stopped.pod_job)
    queue.tick()
    assert (high.status, high.gpu_ids) == (RUNNING, freed)
    # The stopped job waits again, in its place, and starts when a card is free
    assert stopped.status == WAITING and kept.status == RUNNING
    assert "is job high's alone" in stopped.waiting or stopped.waiting.startswith("stopped to make room for job high")
    queue.pods.states[kept.pod_job] = {"state": "completed"}
    queue.tick()
    assert (stopped.status, stopped.starts, stopped.preempted_by) == (RUNNING, 2, None)


def test_work_of_the_same_priority_and_work_the_scheduler_did_not_start_is_waited_for(queue):
    a, b = queue.job("a"), queue.job("b")
    queue.tick()
    same = queue.job("same")
    queue.tick()
    assert (a.status, b.status, same.status) == (RUNNING, RUNNING, WAITING)
    assert same.waiting.startswith("waiting for a GPU of tkamd2")
    # A job on both cards cannot be helped by stopping one lower job while the other card holds outside work
    queue.pods.states[a.pod_job] = {"state": "completed"}
    queue.pods.states[b.pod_job] = {"state": "completed"}
    same.status = STOPPED
    queue.tick()
    low = queue.job("low", priority=5)
    queue.tick()
    assert low.gpu_ids == ["GPU-0"]
    queue.nodes.used = [0.0, 22.0]
    high = queue.job("high", gpus=2, priority=1)
    queue.tick()
    assert low.status == RUNNING and high.status == WAITING and "work the scheduler did not start" in high.waiting


def test_a_job_asked_to_stop_while_it_makes_room_ends(queue):
    low = queue.job("low", priority=4, gpus=2)
    queue.tick()
    high = queue.job("high", priority=2)
    queue.tick()
    assert (low.status, low.preempted_by) == (STOPPING, high.id)
    # A person stops it meanwhile (the API clears what it was stopped for)
    low.preempted_by = None
    queue.pods.live.discard(low.pod_job)
    queue.tick()
    assert low.status == STOPPED and high.status == RUNNING


def test_a_node_limited_to_one_gpu_at_work_gives_no_second_one(queue, test_db):
    from app.models.jobs import NodeLimit

    test_db.add(NodeLimit(node="tkamd2", gpus_at_work=1))
    a, b = queue.job("a", gpu_memory_gb=4), queue.job("b", gpu_memory_gb=4)
    alone = queue.job("alone")
    queue.tick()
    # The two small jobs share the one card at work; the job that wants a card alone waits for the limit
    assert (a.gpu_ids, b.gpu_ids) == (["GPU-0"], ["GPU-0"])
    assert alone.waiting == ("waiting for tkamd2, which is limited to 1 of its GPUs at work at once: 1 hold work")


def test_a_failed_job_says_why_and_a_vanished_one_is_failed(queue):
    a, b = queue.job("a"), queue.job("b")
    queue.tick()
    queue.pods.states[a.pod_job] = {"state": "failed", "error": "the job's process exited with code 1"}
    queue.pods.states[b.pod_job] = {"state": "gone"}
    queue.tick()
    assert (a.status, a.error, a.gpu_ids) == (FAILED, "the job's process exited with code 1", None)
    assert b.status == FAILED and "is gone, and the scheduler did not stop it" in b.error


def test_a_stopped_job_frees_its_gpu_when_its_pod_is_gone(queue):
    a, b, c = queue.job("a"), queue.job("b"), queue.job("c")
    queue.tick()
    a.status = STOPPING
    queue.tick()
    # Its pod is still ending: it holds its card, and c waits
    assert queue.pods.stopped == [a.pod_job] and (a.status, a.gpu_ids) == (STOPPING, ["GPU-0"])
    assert c.status == WAITING
    queue.pods.live.discard(a.pod_job)
    queue.tick()
    assert (a.status, a.gpu_ids) == (STOPPED, None)
    assert (c.status, c.gpu_ids) == (RUNNING, ["GPU-0"])


def test_a_job_that_cannot_start_says_so_and_is_tried_again(queue):
    a = queue.job("a")
    queue.pods.refuse = "Kubernetes refused to create the job job-1: 403 Forbidden"
    queue.tick()
    assert (a.status, a.starts) == (WAITING, 0) and a.waiting.startswith("it could not be started, trying again")
    queue.pods.refuse = None
    queue.nodes.error = "no node-metrics pod is running in thinkube-control"
    queue.tick()
    assert a.waiting == "waiting for the nodes to be read: no node-metrics pod is running in thinkube-control"
    queue.nodes.error = None
    queue.tick()
    assert (a.status, a.starts) == (RUNNING, 1)


def test_a_job_its_node_can_never_run_fails_and_one_without_a_gpu_needs_no_node(queue):
    three = queue.job("three", gpus=3)
    cpu = queue.job("cpu", gpus=0, node="tkamd1")
    queue.tick()
    assert (three.status, three.error) == (FAILED, "tkamd2 has 2 GPUs, and the job asks for 3")
    assert (cpu.status, cpu.gpu_ids) == (RUNNING, [])
    elsewhere = queue.job("elsewhere", node="tkamd9")
    queue.tick()
    assert elsewhere.status == WAITING and "tkamd9: it has no node-metrics pod running" in elsewhere.waiting
