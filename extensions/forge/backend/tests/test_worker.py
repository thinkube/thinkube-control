# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The queue worker's decisions, with Thinkube Control, the run files and the
clock replaced: one job per node, plans before trainings, the order of the
queue, failures that let the queue go on, slots with training mode, the fit
of a run into a slot, two nodes whose slots overlap, priorities against each
kind of work on a node, pre-emption, and a worker that starts again from the
database alone."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.forge import NodeState, Priority, Run, Slot
from app.services import control, runs, worker

MADRID = ZoneInfo("Europe/Madrid")
BASE = {"id": "BSC-LT/salamandraTA-2b-instruct", "name": "SalamandraTA 2B", "params_b": 2.25,
        "context_length": 8192}


def madrid(*args):
    return datetime(*args, tzinfo=MADRID).astimezone(timezone.utc)


class FakeControl:
    def __init__(self):
        self.mode = None
        self.forge_jobs = {}
        self.started = []
        self.calls = []
        self.refusal = None
        # The work outside Forge, as Control lists it
        self.servers_list, self.jobs_list, self.allocations, self.services_list = [], [], {}, []

    async def servers(self):
        return self.servers_list

    async def stop_server(self, node):
        self.calls.append(("stop server", node))
        self.servers_list = [s for s in self.servers_list if s["node"] != node]
        return {}

    async def jobs(self):
        return [*self.jobs_list, *self.forge_jobs.values()]

    async def gpu_status(self):
        return {"nodes": [{"name": n, "allocations": [{"model_id": m} for m in models]}
                          for n, models in self.allocations.items()]}

    async def services(self):
        return self.services_list

    async def training_mode(self):
        return {"on": self.mode is not None, "record": self.mode}

    async def training_mode_on(self, node, reason):
        if self.mode is not None:
            raise control.ControlError(409, f"training mode is on for {self.mode['node']}")
        self.mode = {"node": node, "reason": reason}
        self.calls.append(("on", node))
        # Training mode unloads the node's models and stops its apps
        self.allocations.pop(node, None)
        self.services_list = [s for s in self.services_list if node not in s["gpu_nodes"]]
        return self.mode

    async def training_mode_off(self):
        self.calls.append(("off", self.mode["node"]))
        self.mode = None
        return {}

    async def catalog(self):
        return {"models": [BASE]}

    async def node_choices(self, node):
        return {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16]}

    async def nodes_choices(self):
        return {"tkspark": {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16]},
                "tkamd1": {"memory_gb": [2, 4, 8, 16, 32, 48], "cpu_cores": [1, 2, 4, 6, 8, 12, 16]},
                "tkamd2": {"memory_gb": [2, 4, 8, 16, 32, 48, 64, 96], "cpu_cores": [1, 2, 4, 6, 8, 12, 16, 24, 32]}}

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
        outside = next((j for j in self.jobs_list if j["job_id"] == job_id), None)
        if outside is not None:
            outside["status"] = "cancelled"
            return dict(outside)
        self.forge_jobs[job_id]["status"] = "cancelled"
        return dict(self.forge_jobs[job_id])

    def end(self, job_id, status="completed", error=None):
        self.forge_jobs[job_id].update(status=status, error=error)


class FakeFiles:
    def __init__(self):
        self.plans, self.statuses, self.stops = {}, {}, set()
        self.preprocesses = {}
        self.cleared = []

    async def plan(self, run):
        return self.plans.get(run.id)

    async def preprocess(self, run):
        return self.preprocesses.get(run.id)

    async def status(self, run):
        return self.statuses.get(run.id)

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

        def tick(self, w=None):
            asyncio.run((w or self.worker()).tick(test_db))

        def run(self, name, status="queued", node="tkspark", when="now", position=None, plan=None, **fields):
            fields.setdefault("memory_gb", 32)
            if status != "preprocessing":
                fields.setdefault("preprocess", {"node": "tkamd2", "job_id": "j0", "key": "k", "folder": "/p/k",
                                                 "found": True})
            r = Run(id=str(uuid.uuid4()), name=name, dataset_id="d1", dataset_version=1, base_model=BASE["id"], method="lora",
                    node=node, register=False, job=runs.meepsquared_job("lora", 100, None, {}),
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


def test_a_slot_turns_training_mode_on_starts_its_runs_and_pauses_them_at_its_end(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    run = queue.run("night", when="slots", position=1, across_slots=True, plan={"estimated_seconds": 20 * 3600})
    queue.tick()
    assert run.status == "queued" and queue.control.calls == []

    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert queue.control.calls == [("on", "tkspark")] and queue.control.mode["reason"].startswith("Forge:")
    assert run.status == "running" and run.id in queue.files.cleared

    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.files.statuses[run.id] = {"state": "training", "step": 400, "total_steps": 1000, "seconds_per_step": 70,
                                    "checkpoint": None}
    queue.tick()
    assert run.status == "stopping" and run.paused_by == "slot" and run.id in queue.files.stops
    assert queue.control.mode is not None

    queue.files.statuses[run.id] = {"state": "paused", "step": 410, "total_steps": 1000, "seconds_per_step": 70,
                                    "checkpoint": "output/checkpoint-410"}
    queue.control.end(run.job_id)
    queue.tick()
    assert run.status == "paused" and queue.control.calls[-1] == ("off", "tkspark") and queue.control.mode is None

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


def test_a_slot_without_work_leaves_training_mode_off(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    queue.run("now", position=1)
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert queue.control.calls == []


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
    assert queue.control.mode is None
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick()
    assert now.status == "running" and now.id not in queue.files.stops and queue.control.mode is None


def test_overlapping_slots_of_two_nodes_take_training_mode_one_after_the_other(queue):
    queue.slot("tkspark", [0], "22:00", "07:00")
    queue.slot("tkgpu", [0], "23:00", "09:00")
    spark = queue.run("spark", node="tkspark", when="slots", position=1, plan={"estimated_seconds": 3600})
    gpu = queue.run("gpu", node="tkgpu", when="slots", position=1, plan={"estimated_seconds": 3600})
    queue.clock.now = madrid(2026, 11, 2, 23, 30)
    queue.tick()
    assert queue.control.mode["node"] == "tkspark" and spark.status == "running" and gpu.status == "queued"
    assert "training mode, which is on for tkspark" in gpu.waiting
    queue.files.statuses[spark.id] = {"state": "completed"}
    queue.control.end(spark.job_id)
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick()
    assert queue.control.calls == [("on", "tkspark"), ("off", "tkspark"), ("on", "tkgpu")]
    assert gpu.status == "running"


def test_training_mode_turned_on_by_someone_else_keeps_forge_off_the_node(queue):
    queue.control.mode = {"node": "tkspark", "reason": "a benchmark"}
    run = queue.run("now", position=1)
    other = queue.run("elsewhere", node="tkgpu", position=1)
    queue.tick()
    assert run.status == "queued" and other.status == "running"
    assert "outside Forge" in queue.db.get(NodeState, "tkspark").note


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
    queue.tick()
    assert queue.control.mode is None


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
    assert len(queue.control.started) == 1 and queue.control.calls == [("on", "tkspark")]
    queue.files.statuses[run.id] = {"state": "completed"}
    queue.control.end(run.job_id)
    queue.tick(queue.worker())
    assert run.status == "completed" and waiting.status == "running"
    queue.clock.now = madrid(2026, 11, 3, 7, 0)
    queue.tick(queue.worker())
    assert waiting.status == "stopping"


def test_training_mode_turned_off_outside_forge_is_noted(queue):
    queue.slot("tkspark", [0], "23:00", "07:00")
    queue.run("night", when="slots", position=1, across_slots=True)
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    queue.control.mode = None
    queue.control.mode = {"node": "tkgpu", "reason": "someone"}
    queue.tick()
    assert queue.db.get(NodeState, "tkspark").training_mode is False


# --- priorities --------------------------------------------------------------

def set_priority(queue, kind, priority):
    queue.db.get(Priority, kind).priority = priority
    queue.db.commit()


OUTSIDE = {
    "notebook_server": lambda c: c.servers_list.append({"node": "tkspark", "server_name": "tkspark", "state": "running",
                                                        "gpus": 1}),
    "notebook_job": lambda c: c.jobs_list.append({"job_id": "outside-1234", "node": "tkspark", "status": "running",
                                                  "notebook_path": "lab/eval.ipynb", "gpus": 1}),
    "served_model": lambda c: c.allocations.setdefault("tkspark", []).append("Qwen/Qwen3.5-9B"),
    "app": lambda c: c.services_list.append({"name": "comfy", "display_name": "ComfyUI", "namespace": "comfy",
                                             "gpu_nodes": ["tkspark"]}),
}
NAMES = {"notebook_server": "tkspark", "notebook_job": "outside-",
         "served_model": "Qwen/Qwen3.5-9B", "app": "ComfyUI"}


@pytest.mark.parametrize("kind", sorted(OUTSIDE))
@pytest.mark.parametrize("theirs,outcome", [(2, "waits"), (3, "waits"), (4, "stopped")])
def test_work_of_a_higher_or_equal_priority_makes_a_run_wait_and_lower_is_stopped(queue, kind, theirs, outcome):
    set_priority(queue, kind, theirs)
    OUTSIDE[kind](queue.control)
    run = queue.run("mine", position=1, priority=3)
    queue.tick()
    label = {"notebook_server": "notebook server", "notebook_job": "notebook job", "served_model": "served model",
             "app": "app"}[kind]
    if outcome == "waits":
        assert run.status == "queued" and queue.control.started == [] and queue.control.calls == []
        assert run.waiting.startswith(f"waiting for {label} {NAMES[kind]}")
        assert run.waiting.endswith(f"on tkspark, priority {theirs}")
        return
    stopped = {"notebook_server": ("stop server", "tkspark"), "notebook_job": ("cancel", "outside-1234"),
               "served_model": ("on", "tkspark"), "app": ("on", "tkspark")}[kind]
    assert stopped in queue.control.calls
    queue.tick()
    assert run.status == "running"
    if kind in ("served_model", "app"):
        # Training mode was turned on for the run, and is given back when the node has no Forge work
        assert queue.db.get(NodeState, "tkspark").held_for == "run"
        queue.files.statuses[run.id] = {"state": "completed"}
        queue.control.end(run.job_id)
        queue.tick()
        assert queue.control.calls[-1] == ("off", "tkspark")


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


def test_a_run_does_not_take_training_mode_from_another_node(queue):
    queue.slot("tkgpu", [0], "22:00", "07:00")
    gpu = queue.run("gpu night", node="tkgpu", when="slots", position=1, priority=4)
    queue.control.allocations["tkspark"] = ["Qwen/Qwen3.5-9B"]
    queue.clock.now = madrid(2026, 11, 2, 23, 0)
    queue.tick()
    assert gpu.status == "running" and queue.control.mode["node"] == "tkgpu"
    urgent = queue.run("urgent", position=1, priority=1)
    queue.tick()
    assert urgent.status == "queued" and "training mode, which is on for tkgpu" in urgent.waiting
    assert gpu.status == "running"


def test_the_priorities_of_kinds_are_the_persons(queue):
    from app.services import priorities

    assert priorities.table(queue.db) == {"notebook_server": 1, "notebook_job": 2, "forge_run": 3,
                                          "served_model": 4, "app": 5}
    with pytest.raises(priorities.PriorityError, match="from 1"):
        priorities.set_priorities(queue.db, {"app": 6})
    with pytest.raises(priorities.PriorityError, match="kinds of work"):
        priorities.set_priorities(queue.db, {"printer": 2})


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


def test_work_without_a_gpu_leaves_the_node_to_the_queue(queue):
    queue.control.jobs_list.append({"job_id": "gguf-export", "node": "tkspark", "status": "running", "gpus": 0,
                                    "notebook_path": "job-runs/x.ipynb"})
    queue.control.servers_list.append({"node": "tkspark", "server_name": "tkspark", "state": "running", "gpus": 0})
    run = queue.run("mine", position=1)
    queue.tick()
    assert run.status == "running" and queue.control.calls == []
