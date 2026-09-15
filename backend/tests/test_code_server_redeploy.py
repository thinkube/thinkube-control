"""A code-server redeploy runs its playbook from thinkube-control and answers at once."""

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.code_server as cs
from app.db.init_deployments import INTERRUPTED, mark_interrupted_runs


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *clauses):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.commits = 0

    def query(self, model):
        return Query(self.rows)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


@pytest.fixture
def queued(monkeypatch):
    """Runs go to the run queue; record what was queued instead of starting anything."""
    rows = []

    def enqueue(db, deployment):
        deployment.status = "queued"
        db.add(deployment)
        db.commit()
        rows.append(deployment)
        return len(rows)

    import app.services.run_queue as run_queue

    monkeypatch.setattr(run_queue, "enqueue", enqueue)
    return rows


def test_redeploy_queues_the_playbook_and_answers(queued):
    db = FakeDB()
    answer = asyncio.run(cs.redeploy_code_server(current_user={"preferred_username": "u"}, db=db))
    row = db.added[0]
    assert queued == [row] and row.template_url == "core://code-server/redeploy" and row.status == "queued"
    assert row.variables == {"playbook": "ansible/40_thinkube/core/code-server/20_redeploy.yaml", "component": "code-server"}
    assert answer.deployment_id == str(row.id) and answer.status == "queued" and answer.queue_position == 1
    assert "get_deployment_status" in answer.message


def test_a_redeploy_already_queued_refuses_a_second_one(monkeypatch):
    import app.services.run_queue as run_queue

    def enqueue(db, deployment):
        raise run_queue.DuplicateRun(SimpleNamespace(name="redeploy-code-server", status="running", id="d1"))

    monkeypatch.setattr(run_queue, "enqueue", enqueue)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(cs.redeploy_code_server(current_user={}, db=FakeDB()))
    assert refused.value.status_code == 409 and "d1" in refused.value.detail


def test_the_latest_run_is_reported():
    run = SimpleNamespace(id="d1", status="success", output="Component code-server redeployed successfully",
                          created_at=datetime(2026, 1, 1), completed_at=datetime(2026, 1, 1, 0, 10))
    answer = asyncio.run(cs.get_code_server_redeploy(current_user={}, db=FakeDB([run])))
    assert answer.deployment_id == "d1" and answer.status == "success"
    empty = asyncio.run(cs.get_code_server_redeploy(current_user={}, db=FakeDB()))
    assert empty.deployment_id is None and empty.status is None


def test_the_playbook_skips_the_repository_reset():
    """Redeploy runs every code-server install playbook except the one that resets the workspace repositories."""
    repo = Path(__file__).resolve().parents[3] / "thinkube"
    playbook = repo / cs.REDEPLOY_PLAYBOOK
    if not playbook.exists():
        pytest.skip("the thinkube repository is not next to thinkube-control")
    def imports(path):
        return [line.split(":", 1)[1].strip() for line in path.read_text().splitlines() if line.strip().startswith("import_playbook:")]

    redeploy = imports(playbook)
    install = imports(playbook.parent / "00_install.yaml")
    assert redeploy[0] == "../harbor-images/16_build_codeserver_image.yaml"
    assert redeploy[1:] == [step for step in install if step != "13_clone_repositories.yaml"]
    assert "13_clone_repositories.yaml" in install


def test_runs_left_in_flight_at_startup_are_marked_failed():
    runs = [SimpleNamespace(id="a", name="redeploy-code-server", status="running", output=None, completed_at=None),
            SimpleNamespace(id="b", name="optional-nats", status="pending", output=None, completed_at=None)]
    db = FakeDB(runs)
    assert mark_interrupted_runs(db) == 2
    assert all(r.status == "failed" and r.output == INTERRUPTED and r.completed_at for r in runs)
    assert db.commits == 1
    assert mark_interrupted_runs(FakeDB()) == 0
