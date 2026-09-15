"""Deployment runs start one at a time from the run queue."""

import asyncio
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.background_executor  # noqa: F401  the module, so it can be patched by name
from app.models.deployments import TemplateDeployment
from app.services import run_queue as rq

START = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    TemplateDeployment.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def executor(monkeypatch):
    class Executor:
        def __init__(self):
            self.running_deployments = {}
            self.started = []

        async def execute_component_playbook(self, deployment_id, playbook_path, extra_vars, component):
            self.started.append(("playbook", deployment_id, playbook_path, extra_vars, component))
            self.running_deployments[deployment_id] = object()

        async def start_deployment(self, deployment_id):
            self.started.append(("deployment", deployment_id))
            self.running_deployments[deployment_id] = object()

    fake = Executor()
    monkeypatch.setattr(sys.modules["app.services.background_executor"], "background_executor", fake)
    return fake


def run(name, url, minute, status=None, variables=None):
    return TemplateDeployment(
        id=uuid4(), name=name, template_url=url, status=status or "pending",
        variables=variables or {}, created_by="u", created_at=START + timedelta(minutes=minute),
    )


def test_runs_are_queued_in_order_with_their_positions(db):
    first = run("optional-nats", "optional://nats", 0)
    second = run("redeploy-code-server", "core://code-server/redeploy", 1)
    assert rq.enqueue(db, first) == 1
    assert rq.enqueue(db, second) == 2
    assert first.status == second.status == "queued"
    assert rq.position(db, second) == 2


def test_the_same_run_is_not_queued_twice(db):
    rq.enqueue(db, run("optional-nats", "optional://nats", 0))
    with pytest.raises(rq.DuplicateRun):
        rq.enqueue(db, run("optional-nats", "optional://nats", 1))
    # An uninstall of the same component is a different run.
    assert rq.enqueue(db, run("uninstall-nats", "optional://nats/uninstall", 2)) == 2


def test_a_finished_run_can_be_queued_again(db):
    db.add(run("optional-nats", "optional://nats", 0, status="failed"))
    db.commit()
    assert rq.enqueue(db, run("optional-nats", "optional://nats", 1)) == 1


def test_a_queued_run_can_be_removed_but_not_a_started_one(db):
    queued = run("optional-nats", "optional://nats", 0)
    rq.enqueue(db, queued)
    assert rq.cancel_queued(db, queued) and queued.status == "cancelled"
    started = run("optional-x", "optional://x", 1, status="running")
    db.add(started)
    db.commit()
    assert not rq.cancel_queued(db, started) and started.status == "running"


def test_the_oldest_queued_run_starts_when_nothing_runs(db, executor):
    template = run("my-app", "https://github.com/thinkube/tkt-app", 0)
    playbook = run("optional-nats", "optional://nats", 1,
                   variables={"playbook": "ansible/p.yaml", "component": "nats", "parameters": {"replicas": 3}})
    rq.enqueue(db, template)
    rq.enqueue(db, playbook)
    # The template was queued first.
    started = asyncio.run(rq.run_queue.start_next(db))
    assert started == str(template.id) and template.status == "pending"
    assert executor.started == [("deployment", str(template.id))]
    assert playbook.status == "queued" and rq.position(db, playbook) == 1


def test_nothing_starts_while_a_run_is_in_flight(db, executor):
    rq.enqueue(db, run("optional-nats", "optional://nats", 0, variables={"playbook": "p.yaml", "component": "nats"}))
    executor.running_deployments["other"] = object()
    assert asyncio.run(rq.run_queue.start_next(db)) is None
    executor.running_deployments.clear()
    db.add(run("mirror-1", "image-mirror:docker.io/x", 1, status="running"))
    db.commit()
    assert asyncio.run(rq.run_queue.start_next(db)) is None
    assert executor.started == []


def test_a_playbook_run_starts_with_its_playbook_and_parameters(db, executor):
    queued = run("optional-nats", "optional://nats", 0,
                 variables={"playbook": "ansible/p.yaml", "component": "nats", "parameters": {"replicas": 3}})
    rq.enqueue(db, queued)
    asyncio.run(rq.run_queue.start_next(db))
    assert executor.started == [("playbook", str(queued.id), "ansible/p.yaml", {"replicas": 3}, "nats")]


def test_one_run_starts_at_a_time(db, executor):
    for minute, name in enumerate(["a", "b", "c"]):
        rq.enqueue(db, run(f"optional-{name}", f"optional://{name}", minute, variables={"playbook": "p.yaml", "component": name}))
    asyncio.run(rq.run_queue.start_next(db))
    asyncio.run(rq.run_queue.start_next(db))
    assert len(executor.started) == 1
