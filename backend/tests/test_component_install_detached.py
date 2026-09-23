# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Component installs answer at once and run on the server; their output hides credentials."""

import asyncio
import io
from types import SimpleNamespace

import pytest

import app.services.background_executor  # noqa: F401  the module, so it can be patched by name
import app.api.optional_components as oc
from app.services.scrub import Scrubber


class FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


class FakeService:
    def __init__(self, db, template=None):
        self.template = template

    def get_component(self, name):
        return {"name": name, "display_name": name.upper(), "description": "d", "installed": True}

    def validate_installation(self, name):
        return {"valid": True}

    def template_descriptor(self, name):
        return self.template

    def get_playbook_path(self, name, action):
        return f"/playbooks/{name}/{action}.yaml"


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


def test_install_without_a_body_queues_the_playbook_and_answers(monkeypatch, queued):
    monkeypatch.setattr(oc, "OptionalComponentService", FakeService)
    db = FakeDB()
    answer = asyncio.run(oc.install_optional_component("nats", None, None, current_user={"preferred_username": "u"}, db=db))
    assert answer.status == "queued" and answer.queue_position == 1
    assert answer.websocket_url is None
    assert "get_deployment_status" in answer.message
    row = db.added[0]
    assert queued == [row] and row.template_url == "optional://nats" and db.commits == 1
    assert row.variables["playbook"] == "/playbooks/nats/install.yaml" and row.variables["component"] == "nats"
    assert answer.deployment_id == str(row.id)


def test_install_keeps_the_parameters_for_the_playbook(monkeypatch, queued):
    monkeypatch.setattr(oc, "OptionalComponentService", FakeService)
    body = oc.ComponentInstallRequest(parameters={"replicas": 3})
    asyncio.run(oc.install_optional_component("nats", body, None, current_user={}, db=FakeDB()))
    assert queued[0].variables["parameters"] == {"replicas": 3}


def test_uninstall_queues_its_playbook_and_answers(monkeypatch, queued):
    monkeypatch.setattr(oc, "OptionalComponentService", FakeService)
    db = FakeDB()
    answer = asyncio.run(oc.uninstall_optional_component("nats", current_user={"preferred_username": "u"}, db=db))
    assert answer["status"] == "queued" and answer["queue_position"] == 1 and answer["websocket_url"] is None
    row = db.added[0]
    assert queued == [row] and row.template_url == "optional://nats/uninstall"
    assert row.variables["playbook"] == "/playbooks/nats/uninstall.yaml"


def test_the_same_install_queued_twice_is_refused(monkeypatch):
    from fastapi import HTTPException

    import app.services.run_queue as run_queue

    def enqueue(db, deployment):
        raise run_queue.DuplicateRun(SimpleNamespace(name="optional-nats", status="queued", id="d1"))

    monkeypatch.setattr(run_queue, "enqueue", enqueue)
    monkeypatch.setattr(oc, "OptionalComponentService", FakeService)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(oc.install_optional_component("nats", None, None, current_user={}, db=FakeDB()))
    assert refused.value.status_code == 409 and "d1" in refused.value.detail


def test_a_component_in_flight_reports_its_activity():
    from app.services.optional_components import OptionalComponentService

    class Query:
        def __init__(self, row):
            self.row = row

        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def first(self):
            return self.row

    class DB:
        def __init__(self, row):
            self.row = row

        def query(self, model):
            return Query(self.row)

    service = OptionalComponentService.__new__(OptionalComponentService)
    service.db = DB(SimpleNamespace(template_url="optional://nats/uninstall", status="running"))
    assert service._activity("nats") == "uninstalling"
    service.db = DB(SimpleNamespace(template_url="optional://nats", status="pending"))
    assert service._activity("nats") == "installing"
    service.db = DB(SimpleNamespace(template_url="optional://nats", status="queued"))
    assert service._activity("nats") == "queued"
    service.db = DB(None)
    assert service._activity("nats") is None


def test_injected_values_are_blanked_wherever_they_appear():
    scrub = Scrubber.for_run({"ansible_become_pass": "hunter2xyz", "github_token": "ghp_abcdef", "app_name": "nats"}, {"KUBECONFIG": "/k"})
    assert scrub.clean("sudo password hunter2xyz used for nats") == "sudo password *** used for nats"
    assert scrub.clean('{"token": "ghp_abcdef"}') == '{"token": "***"}'
    assert "nats" in scrub.clean("nats stays")


def test_credential_shapes_are_blanked_by_name():
    scrub = Scrubber()
    assert scrub.clean("password: s3cr3t!") == "password: ***"
    assert scrub.clean('"api_key": "abc123",') == '"api_key": "***",'
    assert scrub.clean("KEYCLOAK_CLIENT_SECRET=xyz other=1") == "KEYCLOAK_CLIENT_SECRET=*** other=1"
    assert scrub.clean("Authorization: Bearer eyJhbGci.x.y") == "Authorization: Bearer ***"
    assert scrub.clean("postgresql://user:pw@db:5432/x") == "postgresql://user:***@db:5432/x"
    assert scrub.clean("TASK [Create the admin password secret]") == "TASK [Create the admin password secret]"
    assert scrub.clean("ok: [tkamd1] => changed=false") == "ok: [tkamd1] => changed=false"


def test_the_venv_runner_blanks_its_lines_too():
    from app.api.jupyter_venvs import read_process_output

    class Stream:
        def __init__(self, data):
            self.data = data

        async def read(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

    log = io.StringIO()
    scrub = Scrubber.for_run({"ansible_ssh_pass": "topsecret9"})
    lines = asyncio.run(read_process_output(Stream(b"login with topsecret9\ntoken=abc\n"), log, scrub))
    assert lines == ["login with ***", "token=***"]
    assert log.getvalue() == "login with ***\ntoken=***\n"


def test_playbook_runs_are_not_verbose():
    from pathlib import Path

    from app.services.ansible_environment import ansible_env

    cmd = ansible_env.get_command_base(Path("/p.yaml"), Path("/inv.yaml"), "/vars.yaml")
    assert "-v" not in cmd and "-vv" not in cmd


def test_the_status_reports_the_step_in_progress(monkeypatch):
    """The step is the last task or phase row; the count is how many there are."""
    from datetime import datetime

    import app.api.templates as templates

    rows = [
        SimpleNamespace(type="task", task_name="Install helm chart", message="TASK [Install helm chart]", timestamp=datetime(2026, 1, 1, 0, 0, 1)),
        SimpleNamespace(type="ok", task_name=None, message="ok: [x]", timestamp=datetime(2026, 1, 1, 0, 0, 2)),
        SimpleNamespace(type="task", task_name="Wait for pods", message="TASK [Wait for pods]", timestamp=datetime(2026, 1, 1, 0, 0, 3)),
        SimpleNamespace(type="failed", task_name=None, message="fatal: timed out", timestamp=datetime(2026, 1, 1, 0, 0, 4)),
    ]

    class Query:
        def __init__(self, rows):
            self.rows = rows
            self.types = None

        def filter(self, *clauses):
            # The only filters used are on type; the deployment id clause is ignored here.
            kept = self.rows
            for c in clauses:
                right = getattr(c, "right", None)
                values = getattr(right, "value", None)
                if isinstance(values, (list, tuple)):
                    kept = [r for r in kept if r.type in values]
            return Query(kept)

        def order_by(self, *a):
            return Query(sorted(self.rows, key=lambda r: r.timestamp, reverse=True))

        def limit(self, n):
            return Query(self.rows[:n])

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

        def count(self):
            return len(self.rows)

    deployment = SimpleNamespace(
        id="d1", status="failed",
        to_dict=lambda: dict(id="d1", name="optional-nats", template_url="optional://nats", status="failed", variables={}, output=None,
                             created_at=datetime(2026, 1, 1), started_at=None, completed_at=None, created_by="u", duration=None),
    )

    class DB:
        def query(self, model):
            if model is templates.DeploymentLog:
                return Query(rows)
            return SimpleNamespace(filter_by=lambda **k: SimpleNamespace(first=lambda: deployment))

    answer = asyncio.run(templates.get_deployment_status("d1", current_user={"realm_access": {"roles": ["admin"]}}, db=DB()))
    assert answer.current_step == "Wait for pods"
    assert answer.steps_done == 2
    assert answer.reason == "fatal: timed out"
