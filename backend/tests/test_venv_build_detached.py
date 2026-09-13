"""The venv build endpoint answers before the build runs, and the build still runs."""

import asyncio
import uuid
from types import SimpleNamespace

import app.api.jupyter_venvs as jv
from app.api.jupyter_venvs import BuildVenvRequest
from app.services import detached


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    def query(self, model):
        return FakeQuery(self.row)

    def commit(self):
        self.commits += 1


def test_build_returns_while_the_build_is_still_running(monkeypatch):
    venv = SimpleNamespace(id=uuid.uuid4(), name="c17", status="pending", output=None, started_at=None, completed_at=None)
    db = FakeDB(venv)
    build_started = asyncio.Event()
    build_may_finish = asyncio.Event()
    finished = []

    async def fake_build(venv_id):
        build_started.set()
        await build_may_finish.wait()
        finished.append(venv_id)

    monkeypatch.setattr(jv, "_execute_venv_build", fake_build)

    async def scenario():
        response = await jv.build_jupyter_venv(venv.id, BuildVenvRequest(force=True), db=db, current_user={})
        # The answer is back; the build has started but not finished.
        assert response.status == "building"
        assert response.build_id == str(venv.id)
        await asyncio.wait_for(build_started.wait(), 1)
        assert finished == []
        assert detached.running(f"venv-build:{venv.id}")
        build_may_finish.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert finished == [str(venv.id)]
        assert not detached.running(f"venv-build:{venv.id}")

    asyncio.run(scenario())
    assert venv.status == "building"
    assert db.commits == 1


def test_build_without_a_body_is_the_plain_build(monkeypatch):
    venv = SimpleNamespace(id=uuid.uuid4(), name="c17", status="pending", output=None, started_at=None, completed_at=None)
    started = []

    async def fake_build(venv_id):
        started.append(venv_id)

    monkeypatch.setattr(jv, "_execute_venv_build", fake_build)

    async def scenario():
        response = await jv.build_jupyter_venv(venv.id, None, db=FakeDB(venv), current_user={})
        assert response.status == "building"
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert started == [str(venv.id)]


def test_a_failing_detached_task_is_logged_not_lost(caplog):
    async def boom():
        raise RuntimeError("the build blew up")

    async def scenario():
        task = detached.start("venv-build:test", boom())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert task.done()

    asyncio.run(scenario())
    assert any("the build blew up" in r.getMessage() for r in caplog.records)


def test_builds_orphaned_by_a_restart_are_marked_failed():
    from app.db.init_venvs import mark_orphaned_builds

    building = SimpleNamespace(name="a", status="building", output=None, completed_at=None)
    done = SimpleNamespace(name="b", status="success", output="ok", completed_at=None)

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return Query([r for r in self.rows if r.status == "building"])

        def all(self):
            return self.rows

    class DB:
        commits = 0

        def query(self, model):
            return Query([building, done])

        def commit(self):
            self.commits += 1

    db = DB()
    assert mark_orphaned_builds(db) == 1
    assert building.status == "failed" and "restarted" in building.output
    assert done.status == "success"
    assert db.commits == 1


def test_a_template_build_tells_the_playbook_so(monkeypatch):
    """The playbook rebuilds a template in place; it must know which kind it builds."""
    import app.api.jupyter_venvs as jv_mod

    seen = {}

    class FakeProc:
        returncode = 0
        stdout = None

    async def fake_exec(*cmd, **kwargs):
        # The vars file is the last -e argument.
        import yaml
        vars_path = [a for a in cmd if a.startswith("@")][0][1:]
        seen.update(yaml.safe_load(open(vars_path)))
        class Out:
            async def readline(self):
                return b""
        proc = FakeProc(); proc.stdout = Out()
        async def wait():
            return 0
        proc.wait = wait
        return proc

    monkeypatch.setattr(jv_mod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(jv_mod.Path, "exists", lambda self: True)
    monkeypatch.setattr(jv_mod.ansible_env, "prepare_auth_vars", lambda v: v)
    monkeypatch.setattr(jv_mod.ansible_env, "get_inventory_path", lambda: "/tmp/inv.yaml")
    monkeypatch.setattr(jv_mod.ansible_env, "get_environment", lambda context="template": {})

    template = SimpleNamespace(name="agent-dev", packages=["a"], is_template=True)
    asyncio.run(jv_mod._run_ansible_build(template))
    assert seen["is_template"] is True and seen["venv_name"] == "agent-dev"

    custom = SimpleNamespace(name="mine", packages=["a"], is_template=False)
    asyncio.run(jv_mod._run_ansible_build(custom))
    assert seen["is_template"] is False
