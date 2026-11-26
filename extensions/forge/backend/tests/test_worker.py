# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The queue worker's decisions, with the Scheduler's jobs, the run files and the clock replaced: one of Forge's
runs per node, plans before trainings, the order of the queue, failures that let the queue go on, slots, the fit
of a run into a slot, two nodes whose slots overlap, priorities among Forge's runs, pre-emption, what a job waits
for at the Scheduler, and a worker that starts again from the database alone. The work outside Forge is the
Scheduler's to weigh."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.forge import Run, Slot
from app.services import control, runfiles, runs, worker

MADRID = ZoneInfo("Europe/Madrid")
BASE = {"id": "BSC-LT/salamandraTA-2b-instruct", "name": "SalamandraTA 2B", "params_b": 2.25,
        "context_length": 8192}


def madrid(*args):
    return datetime(*args, tzinfo=MADRID).astimezone(timezone.utc)


class FakeControl:
    """Thinkube Control and the Scheduler as the worker calls them: the catalogue, the nodes' choices, and the runs'
    jobs. The work outside Forge (servers_list, allocations) is there to show the worker does not read it."""

    def __init__(self):
        self.forge_jobs = {}
        self.started = []
        self.calls = []
        self.refusal = None
        self.servers_list, self.allocations = [], {}

    async def catalog(self):
        return {"models": [BASE]}

    async def node_choices(self, node):
        return {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16], "gpus": [0, 1, 2]}

    async def nodes_choices(self):
        return {"tkspark": {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16],
                            "gpus": [0, 1]},
                "tkamd1": {"memory_gb": [2, 4, 8, 16, 32, 48], "cpu_cores": [1, 2, 4, 6, 8, 12, 16], "gpus": [0]},
                "tkamd2": {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16, 24, 32],
                           "gpus": [0, 1, 2]}}

    async def run_job(self, request):
        if self.refusal is not None:
            raise self.refusal
        job_id = f"j{len(self.started) + 1}"
        self.started.append(request)
        self.forge_jobs[job_id] = {"job_id": job_id, "status": "starting", "error": None, "node": request["node"]}
        return dict(self.forge_jobs[job_id])

    async def job(self, job_id):
        return dict(self.forge_jobs[job_id])

    async def cancel_job(self, job_id):
        self.calls.append(("cancel", job_id))
        self.forge_jobs[job_id]["status"] = "cancelled"
        return dict(self.forge_jobs[job_id])

    def end(self, job_id, status="completed", error=None):
        self.forge_jobs[job_id].update(status=status, error=error)


class FakeFiles:
    def __init__(self):
        self.plans, self.statuses, self.stops = {}, {}, set()
        self.scores, self.unreadable_scores = {}, set()
        self.preprocesses = {}
        self.revisions = {}
        self.cleared = []

    async def plan(self, run):
        return self.plans.get(run.id)

    async def preprocess(self, run):
        return self.preprocesses.get(run.id)

    async def revision(self, run):
        return self.revisions.get(run.id)

    async def status(self, run):
        return self.statuses.get(run.id)

    async def tagging_scores(self, run):
        if run.id in self.unreadable_scores:
            raise runfiles.RunFilesError("MLflow answered the download with 504")
        return self.scores.get(run.id)

    async def request_stop(self, run):
        self.stops.add(run.id)

    async def clear_stop(self, run):
        self.stops.discard(run.id)
        self.cleared.append(run.id)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def queue(test_db):
    fake, files, clock = FakeControl(), FakeFiles(), Clock(madrid(2026, 11, 2, 12, 0))

    class Queue:
        db = test_db
        control = fake

        def __init__(self):
            self.files, self.clock = files, clock

        def worker(self):
            return worker.Worker(fake, files, clock)

        def task(self, name, node="tkspark", priority=3, position=None, status="queued", gpus=1):
            """A task run of Mill, as Forge's API creates it."""
            r = Run(id=str(uuid.uuid4()), name=name, kind="task", node=node, when="now", status=status,
                    priority=priority, position=position, memory_gb=8, cpu_cores=2, created_by="u",
                    created_at=clock.now,
                    task={"file": "demo/clean.py", "task": None, "inputs": {"rows": "data/rows.jsonl"},
                          "outputs": {"cleaned": "data/cleaned.jsonl"}, "settings": {}, "limit": None,
                          "gpus": gpus, "attempts": 0, "mill_run": None})
            test_db.add(r)
            test_db.commit()
            return r

        def tick(self, w=None):
            asyncio.run((w or self.worker()).tick(test_db))

        def run(self, name, status="queued", node="tkspark", when="now", position=None, plan=None, **fields):
            fields.setdefault("memory_gb", 32)
            fields.setdefault("job", runs.meepsquared_job("lora", 100, None, {}))
            if status != "preprocessing":
                fields.setdefault("preprocess", {"node": "tkamd2", "job_id": "j0", "key": "k", "folder": "/p/k",
                                                 "found": True})
            r = Run(id=str(uuid.uuid4()), name=name, dataset_id="d1", dataset_version=1, base_model=BASE["id"], method="lora",
                    node=node, register=False,
                    status=status, when=when, position=position, plan=plan or {"estimated_seconds": 3600},
                    mlflow_experiment_id="5", mlflow_run_id=f"m-{name}",
                    dataset={"train": {"split": "train", "path": "p", "rows": 1}}, created_by="u",
                    created_at=clock.now, **fields)
            test_db.add(r)
            test_db.commit()
            return r

        def slot(self, node, days, start, end):
            s = Slot(id=str(uuid.uuid4()), node=node, days=days, start=start, end=end, timezone="Europe/Madrid",
                     created_by="u")
            test_db.add(s)
            test_db.commit()
            return s

        def mode_of(self, run):
            return self.control.started[[r["parameters"]["run_id"] for r in self.control.started].index(run.id)][
                "parameters"]["mode"]

    return Queue()


def test_a_new_run_has_its_data_preprocessed_on_a_cpu_node_then_plans(queue):
    run = queue.run("a", status="preprocessing", plan=None)
    queue.tick()
    request = queue.control.started[0]
    assert run.preprocess == {"node": "tkamd2", "job_id": "j1"} and run.status == "preprocessing"
    assert (request["node"], request["gpus"], request["cpu_cores"], request["parameters"]["mode"]) == \
        ("tkamd2", 0, 32, "preprocess")
    queue.tick()
    assert len(queue.control.started) == 1
    # The outcome the job wrote joins the run, and the plan job follows, reading the folder
    queue.files.preprocesses[run.id] = {"key": "k9", "folder": "/p/k9", "found": False, "report": {"rows": 3}}
    queue.control.end("j1")
    queue.tick()
    assert run.status == "planning" and run.preprocess["folder"] == "/p/k9" and run.preprocess["node"] == "tkamd2"
    queue.tick()
    assert run.plan_job_id == "j2" and queue.control.started[1]["parameters"]["preprocessed"] == "/p/k9"


def test_a_preprocessing_job_with_no_room_on_its_node_moves_to_the_runs_own_node(queue):
    run = queue.run("a", status="preprocessing", plan=None)
    queue.tick()
    assert run.preprocess == {"node": "tkamd2", "job_id": "j1"}
    queue.control.forge_jobs["j1"]["waiting"] = "the pod waits for room on its node: 1 Insufficient memory"
    queue.tick()
    assert ("cancel", "j1") in queue.control.calls
    assert run.preprocess == {"node": "tkspark", "job_id": "j2", "tried": ["tkamd2", "tkspark"]}
    assert queue.control.started[1]["node"] == "tkspark" and queue.control.started[1]["gpus"] == 0
    # On the last node it stays and waits, and the run says what for
    queue.control.forge_jobs["j2"]["waiting"] = "the pod waits for room on its node: 1 Insufficient memory"
    queue.tick()
    assert run.preprocess["job_id"] == "j2" and len(queue.control.started) == 2
    assert run.status == "preprocessing" and "waits for room" in run.waiting
    queue.control.forge_jobs["j2"]["waiting"] = None
    queue.files.preprocesses[run.id] = {"key": "k9", "folder": "/p/k9", "found": False, "report": {"rows": 3}}
    queue.control.end("j2")
    queue.tick()
    assert run.status == "planning" and run.preprocess["node"] == "tkspark"


def test_a_preprocessing_job_that_fails_fails_its_run(queue):
    run = queue.run("a", status="preprocessing", plan=None)
    queue.tick()
    queue.control.end("j1", "error")
    queue.tick()
    assert run.status == "failed" and "preprocessing failed" in run.error


def test_a_planning_run_gets_its_plan_job_then_its_plan(queue):
    run = queue.run("a", status="planning", plan=None)
    queue.tick()
    assert run.plan_job_id == "j1" and queue.mode_of(run) == "plan"
    queue.tick()
    assert len(queue.control.started) == 1 and run.status == "planning"
    queue.files.plans[run.id] = {"decisions": [], "estimated_seconds": 900}
    queue.control.end("j1")
    queue.tick()
    assert run.status == "planned" and run.plan["estimated_seconds"] == 900
    assert len(queue.control.started) == 1


def test_a_run_asked_to_queue_on_its_plan_joins_the_end_of_its_queue(queue):
    queue.run("first", position=1)
    run = queue.run("b", status="planning", plan=None, queue_on_plan=True)
    queue.tick()
    queue.files.plans[run.id] = {"decisions": []}
    queue.control.end("j1")
    queue.tick()
    assert (run.status, run.position) == ("queued", 2)


MEASURED = {"decisions": [], "estimated_seconds": 3600, "measurements": {"key": {}, "device": "RTX 3090"}}


def preprocessed(queue, run, job_id):
    """The run's preprocessing job started, then completed with its outcome."""
    queue.tick()
    queue.files.preprocesses[run.id] = {"key": "k9", "folder": "/p/k9", "found": True, "report": {"rows": 3}}
    queue.control.end(job_id)
    queue.tick()


def test_a_plan_reuses_the_last_plans_measurements_on_a_cpu(queue):
    reuse = {"plan": MEASURED, "job_id": "j0", "run": "b", "node": "tkspark"}
    run = queue.run("b", status="preprocessing", plan=None, reuse=reuse, queue_on_plan=True)
    preprocessed(queue, run, "j1")
    assert (run.status, run.revises) == ("revising", "j0")
    queue.tick()
    request = queue.control.started[1]
    assert run.plan_job_id == "j2"
    assert (request["node"], request["gpus"], request["memory_gb"], request["parameters"]["mode"]) == \
        ("tkamd2", 0, 16, "revise")
    assert request["parameters"]["earlier_plan"] == MEASURED
    assert request["parameters"]["reused_from"] == "Forge's plan job j0 of run b"
    assert request["parameters"]["preprocessed"] == "/p/k9"
    queue.tick()
    assert len(queue.control.started) == 2
    queue.files.revisions[run.id] = {"plan": {"decisions": [{"name": "measurements"}], "estimated_seconds": 3700}}
    queue.control.end("j2")
    queue.tick()
    # Queued on its plan, and started at once: the node is free
    assert (run.status, run.plan["estimated_seconds"]) == ("running", 3700)
    assert queue.control.started[-1]["parameters"]["mode"] == "train"


def test_measurements_that_do_not_hold_are_measured_again_on_the_gpu(queue):
    reuse = {"plan": MEASURED, "job_id": "j0", "run": "b", "node": "tkspark"}
    run = queue.run("b", status="preprocessing", plan=None, reuse=reuse)
    preprocessed(queue, run, "j1")
    queue.tick()
    queue.files.revisions[run.id] = {"measure_again": "the LoRA rank changed"}
    queue.control.end("j2")
    queue.tick()
    # The plan job on the GPU starts in the same tick: the node is free
    assert (run.status, run.plan_job_id, run.reuse["measure_again"]) == ("planning", "j3", "the LoRA rank changed")
    request = queue.control.started[-1]
    assert (request["parameters"]["mode"], request["gpus"]) == ("plan", 1)
    assert request["parameters"]["measured_because"] == \
        "the measurements of Forge's plan job j0 of run b do not hold: the LoRA rank changed"


def test_a_first_plan_reuses_another_runs_on_its_node_and_a_person_may_ask_to_measure(queue):
    start = queue.clock.now
    queue.run("older", status="completed", plan=MEASURED, plan_job_id="p1")
    queue.clock.now = start + timedelta(minutes=1)
    queue.run("newer", status="planned", plan=MEASURED, plan_job_id="p2")
    # Newer still, and not reused: on another node's GPU, or without measurements
    queue.clock.now = start + timedelta(minutes=2)
    queue.run("elsewhere", status="completed", node="tkamd2", plan=MEASURED, plan_job_id="p3")
    queue.run("unmeasured", status="completed", plan={"decisions": []}, plan_job_id="p4")
    run = queue.run("first", status="preprocessing", plan=None)
    preprocessed(queue, run, "j1")
    assert (run.status, run.revises, run.reuse["run"]) == ("revising", "p2", "newer")
    asked = queue.run("asked", status="preprocessing", plan=None, measure=True)
    preprocessed(queue, asked, "j3")
    assert asked.status == "planning" and asked.reuse is None


def test_a_revision_job_that_fails_fails_its_run(queue):
    run = queue.run("a", status="revising", plan=None, revises="j0",
                    reuse={"plan": MEASURED, "job_id": "j0", "run": "a", "node": "tkspark"})
    queue.tick()
    queue.control.end("j1", "error", "KeyError")
    queue.tick()
    assert run.status == "failed" and "plan from earlier measurements failed" in run.error


def test_a_plan_job_that_fails_fails_its_run(queue):
    run = queue.run("a", status="planning", plan=None)
    queue.tick()
    queue.control.end("j1", "error", "CUDA out of memory")
    queue.tick()
    assert run.status == "failed" and "CUDA out of memory" in run.error


def test_meepsquareds_refusal_of_a_plan_is_shown_as_the_plans(queue):
    run = queue.run("fp8", status="planning", plan=None)
    queue.tick()
    queue.control.end("j1", "error", "cell 4 failed")
    queue.control.forge_jobs["j1"]["results"] = [
        {"cell_index": 4, "success": False, "outputs": [
            {"output_type": "stream", "text": "loading\n"},
            {"output_type": "error", "ename": "ValueError", "evalue": "method='fp8' needs FP8 tensor cores"}]}]
    queue.tick()
    assert run.error == "Meep²'s plan failed: ValueError: method='fp8' needs FP8 tensor cores"


def test_one_training_per_node_in_the_queues_order(queue):
    second = queue.run("second", position=2)
    first = queue.run("first", position=1)
    other = queue.run("other node", node="tkgpu", position=1)
    queue.tick()
    assert (first.status, second.status) == ("running", "queued") and first.job_id is not None
    assert other.status == "running"
    queue.tick()
    assert len(queue.control.started) == 2
    queue.files.statuses[first.id] = {"state": "completed", "step": 100, "total_steps": 100}
    queue.control.end(first.job_id)
    queue.tick()
    # An ended run leaves the queue
    assert first.status == "completed" and first.finished_at is not None and first.position is None
    assert second.status == "running"


def test_a_plan_job_holds_the_node_too(queue):
    queue.run("planning", status="planning", plan=None)
    queued = queue.run("queued", position=1)
    queue.tick()
    queue.tick()
    assert queued.status == "queued" and len(queue.control.started) == 1


def test_a_failed_training_is_recorded_and_the_queue_goes_on(queue):
    first = queue.run("first", position=1)
    second = queue.run("second", position=2)
    queue.tick()
    queue.files.statuses[first.id] = {"state": "failed", "error": "loss is NaN at step 12"}
    queue.control.end(first.job_id, "error", "cell 5 raised")
    queue.tick()
    assert first.status == "failed" and "loss is NaN at step 12" in first.error
    assert second.status == "running"


def test_a_refused_start_fails_the_run_and_a_server_error_waits(queue):
    first = queue.run("first", position=1)
    second = queue.run("second", position=2)
    queue.control.refusal = control.ControlError(502, "Thinkube Control did not answer")
    queue.tick()
    assert first.status == "queued" and "did not answer" in first.error
    queue.control.refusal = control.ControlError(422, "node 'tkspark' has no free GPU")
    queue.tick()
    assert first.status == "failed" and second.status == "queued"
    queue.tick()
    assert second.status == "failed"
    queue.control.refusal = None
    third = queue.run("third", position=3)
    queue.tick()
    assert third.status == "running"


def test_a_person_pausing_a_queued_run_holds_it(queue):
    held = queue.run("held", status="paused", position=1, paused_by="user")
    after = queue.run("after", position=2)
    queue.tick()
    assert held.status == "paused" and after.status == "running"


def test_a_slot_starts_its_runs_and_pauses_them_at_its_end(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    run = queue.run("night", when="slots", position=1, across_slots=True, plan={"estimated_seconds": 20 * 3600})
    queue.tick()
    assert run.status == "queued" and queue.control.started == []

    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert run.status == "running" and run.id in queue.files.cleared

    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.files.statuses[run.id] = {"state": "training", "step": 400, "total_steps": 1000, "seconds_per_step": 70,
                                    "checkpoint": None}
    queue.tick()
    assert run.status == "stopping" and run.paused_by == "slot" and run.id in queue.files.stops

    queue.files.statuses[run.id] = {"state": "paused", "step": 410, "total_steps": 1000, "seconds_per_step": 70,
                                    "checkpoint": "output/checkpoint-410"}
    queue.control.end(run.job_id)
    queue.tick()
    assert run.status == "paused" and queue.control.calls == []

    # The next slot opens on the following Monday, and the run goes on from its checkpoint
    queue.clock.now = madrid(2026, 11, 9, 23, 0)
    queue.tick()
    assert run.status == "running" and run.job_id == "j2"
    assert queue.control.started[1]["parameters"]["mode"] == "train" and queue.files.cleared.count(run.id) == 2


def test_a_run_that_does_not_fit_the_slot_waits_unless_it_goes_across_slots(queue):
    queue.slot("tkspark", [0], "23:00", "01:00")
    long = queue.run("long", when="slots", position=1, plan={"estimated_seconds": 3 * 3600})
    short = queue.run("short", when="slots", position=2, plan={"estimated_seconds": 3600})
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert long.status == "queued" and short.status == "running"
    queue.files.statuses[short.id] = {"state": "completed"}
    queue.control.end(short.job_id)
    queue.clock.now = madrid(2026, 11, 2, 23, 30)
    queue.tick()
    assert long.status == "queued"
    long.across_slots = True
    queue.db.commit()
    queue.tick()
    assert long.status == "running"


def test_a_run_for_slots_without_an_estimate_fails_with_why(queue):
    queue.slot("tkspark", [0], "23:00", "01:00")
    run = queue.run("unknown", when="slots", position=1, plan={"decisions": []})
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert run.status == "failed" and "estimated_seconds" in run.error


def test_a_run_now_does_not_wait_for_a_slot_and_a_slot_end_does_not_stop_it(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    night = queue.run("night", when="slots", position=1)
    now = queue.run("now", position=2)
    queue.tick()
    assert now.status == "running" and night.status == "queued"
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    # Same priority: the slot's run waits for the run that holds the node
    assert night.status == "queued" and night.waiting == "waiting for run now on tkspark, priority 3"
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick()
    assert now.status == "running" and now.id not in queue.files.stops


def test_overlapping_slots_of_two_nodes_start_their_runs_at_once(queue):
    queue.slot("tkspark", [0], "22:00", "07:00")
    queue.slot("tkgpu", [0], "23:00", "09:00")
    spark = queue.run("spark", node="tkspark", when="slots", position=1, plan={"estimated_seconds": 3600})
    gpu = queue.run("gpu", node="tkgpu", when="slots", position=1, plan={"estimated_seconds": 3600})
    queue.clock.now = madrid(2026, 11, 2, 23, 30)
    queue.tick()
    assert (spark.status, gpu.status) == ("running", "running")


def test_a_run_that_does_not_pause_in_time_is_cancelled_so_the_node_comes_back(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    run = queue.run("stuck", when="slots", position=1, across_slots=True)
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick()
    assert run.status == "stopping"
    queue.clock.now = madrid(2026, 11, 3, 7, 0) + timedelta(seconds=worker.STOP_GRACE_SECONDS + 1)
    queue.tick()
    assert run.status == "failed" and ("cancel", run.job_id) in queue.control.calls


def test_a_new_worker_re_attaches_to_running_jobs_from_the_database(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    run = queue.run("night", when="slots", position=1, across_slots=True)
    waiting = queue.run("next", when="slots", position=2, across_slots=True)
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert run.status == "running"
    queue.db.expire_all()
    # A push replaced the pod: a new worker, with nothing but the database and Control
    fresh = queue.worker()
    queue.clock.now = madrid(2026, 11, 3, 2, 0)
    queue.tick(fresh)
    assert len(queue.control.started) == 1 and queue.control.calls == []
    queue.files.statuses[run.id] = {"state": "completed"}
    queue.control.end(run.job_id)
    queue.tick(queue.worker())
    assert run.status == "completed" and waiting.status == "running"
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick(queue.worker())
    assert waiting.status == "stopping"


# --- priorities --------------------------------------------------------------

def test_work_outside_forge_is_the_schedulers_to_weigh(queue):
    """A notebook server, a served model or an app on the node is not Forge's to wait for or to stop: the run's
    job goes to the Scheduler, which gives it its turn among them."""
    queue.control.servers_list.append({"node": "tkspark", "server_name": "tkspark", "state": "running", "gpus": 1})
    queue.control.allocations.setdefault("tkspark", []).append("Qwen/Qwen3.5-9B")
    run = queue.run("mine", position=1, priority=3)
    queue.tick()
    assert run.status == "running" and queue.control.calls == []
    request = queue.control.started[-1]
    assert (request["priority"], request["resumable"], request["name"]) == (3, True, "Forge mine: train")


def test_a_run_says_what_its_job_waits_for_at_the_scheduler(queue):
    run = queue.run("mine", position=1)
    queue.tick()
    queue.control.forge_jobs[run.job_id]["waiting"] = "waiting for 2 GPUs of tkspark (a GPU alone): GPU 0 holds job x"
    queue.tick()
    assert run.status == "running" and run.waiting.startswith("waiting for 2 GPUs of tkspark")
    queue.control.forge_jobs[run.job_id].update(status="running", waiting=None)
    queue.tick()
    assert run.waiting is None


@pytest.mark.parametrize("theirs,outcome", [(2, "waits"), (3, "waits"), (4, "stopped")])
def test_a_forge_run_of_a_higher_or_equal_priority_makes_a_run_wait_and_lower_is_paused(queue, theirs, outcome):
    other = queue.run("other", position=1, priority=theirs)
    queue.tick()
    assert other.status == "running"
    run = queue.run("mine", position=2, priority=3)
    queue.tick()
    if outcome == "waits":
        assert run.status == "queued" and run.waiting == f"waiting for run other on tkspark, priority {theirs}"
        assert other.status == "running"
    else:
        assert (other.status, other.paused_by, other.preempted_by) == ("stopping", "preempted", "mine")
        assert other.id in queue.files.stops and run.waiting.startswith("making room: stopping run other")


def test_a_higher_run_added_later_preempts_a_running_one_which_resumes_after(queue):
    low = queue.run("low", position=1, priority=3)
    queue.tick()
    assert low.status == "running"
    queue.files.statuses[low.id] = {"state": "training", "step": 50, "total_steps": 100}
    urgent = queue.run("urgent", position=2, priority=1)
    queue.tick()
    assert (low.status, low.preempted_by) == ("stopping", "urgent") and urgent.status == "queued"
    queue.files.statuses[low.id] = {"state": "paused", "step": 51, "total_steps": 100, "checkpoint": "out/checkpoint-51"}
    queue.control.end(low.job_id)
    queue.tick()
    assert (low.status, low.paused_by, low.preempted_by) == ("paused", "preempted", "urgent")
    assert urgent.status == "running"
    queue.tick()
    assert low.waiting == "waiting for run urgent on tkspark, priority 1"
    queue.files.statuses[urgent.id] = {"state": "completed"}
    queue.control.end(urgent.job_id)
    queue.tick()
    assert low.status == "running" and low.preempted_by is None
    assert queue.control.started[-1]["parameters"]["mode"] == "train" and queue.files.cleared.count(low.id) == 2


def test_the_queue_order_is_by_priority_then_position(queue):
    first = queue.run("first", position=1, priority=4)
    second = queue.run("second", position=2, priority=2)
    queue.tick()
    assert second.status == "running" and first.status == "queued"


def test_a_training_is_sized_by_meepsquareds_plan(queue):
    run = queue.run("sized", position=1, memory_gb=None, plan={"memory_request_bytes": 35 * 2**30})
    queue.tick()
    # 35 GiB, raised to the node's next memory choice
    assert run.status == "running" and queue.control.started[-1]["memory_gb"] == 48


def test_a_plan_that_cannot_size_the_training_is_not_queued(queue):
    run = queue.run("unsized", status="planning", plan=None, queue_on_plan=True, memory_gb=None)
    queue.tick()
    queue.files.plans[run.id] = {"memory_request_bytes": None, "decisions": [
        {"name": "memory", "text": "unknown", "reason": "no GPU to measure on"}]}
    queue.control.end("j1")
    queue.tick()
    assert run.status == "planned" and "no GPU to measure on" in run.error


# --- Mill's task runs ------------------------------------------------------


def test_a_task_run_kept_from_before_is_not_forges_to_run(queue):
    """Mill gives its tasks to the Scheduler itself; a task run Forge kept is neither started nor followed."""
    old = queue.task("old", position=1)
    fine_tune = queue.run("mine", position=2)
    queue.tick()
    assert old.status == "queued" and fine_tune.status == "running"
    assert [r["parameters"]["run_id"] for r in queue.control.started] == [fine_tune.id]


def test_a_tagging_runs_progress_holds_the_scores_of_each_epoch(queue):
    run = queue.run("tagger", position=1, job=runs.meepsquared_job("bottleneck", None, 20, {}, task="tagging"))
    queue.tick()
    queue.files.statuses[run.id] = {"state": "training", "step": 900, "total_steps": 9000}
    queue.tick()
    assert run.progress["epoch_scores"] == []
    epoch = {"epoch": 1, "step": 450, "upos": 0.97, "uas": 0.88, "las": 0.84, "words": 12000, "score": 0.9}
    queue.files.scores[run.id] = [epoch]
    queue.tick()
    assert run.progress["epoch_scores"] == [epoch] and run.progress["step"] == 900


def test_a_tagging_runs_progress_waits_while_its_scores_cannot_be_read(queue):
    run = queue.run("tagger", position=1, job=runs.meepsquared_job("bottleneck", None, 20, {}, task="tagging"))
    queue.tick()
    queue.files.statuses[run.id] = {"state": "training", "step": 900, "total_steps": 9000}
    queue.files.scores[run.id] = [{"epoch": 1, "las": 0.84}]
    queue.tick()
    queue.files.unreadable_scores.add(run.id)
    queue.files.statuses[run.id] = {"state": "training", "step": 1800, "total_steps": 9000}
    queue.tick()
    assert run.progress["step"] == 900 and run.progress["epoch_scores"] == [{"epoch": 1, "las": 0.84}]


def test_a_tagging_run_plans_on_its_data_files_with_no_preprocessed_folder(queue):
    run = queue.run("tagger", status="preprocessing", plan=None,
                    job=runs.meepsquared_job("bottleneck", None, 20, {}, task="tagging"))
    queue.tick()
    queue.files.preprocesses[run.id] = {"key": None, "folder": None, "found": False,
                                        "report": {"decision": {"text": "a tagging job encodes its sentences"}}}
    queue.control.end("j1")
    queue.tick()
    # No revision: it reads preprocessed rows, which a tagging job has none of
    assert run.status == "planning" and run.reuse is None
    queue.tick()
    plan = queue.control.started[1]["parameters"]
    assert plan["mode"] == "plan" and plan["preprocessed"] is None
