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
