# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The venv build endpoint answers before the build runs, and the build still runs."""

import asyncio
import uuid
from types import SimpleNamespace

import app.api.jupyter_venvs as jv
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


def test_a_build_this_process_still_runs_is_left_alone():
    from app.db.init_venvs import mark_orphaned_builds

    mine = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", name="mine", status="building", output=None, completed_at=None)
    lost = SimpleNamespace(id="22222222-2222-2222-2222-222222222222", name="lost", status="building", output=None, completed_at=None)

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def all(self):
            return self.rows

    class DB:
        commits = 0

        def query(self, model):
            return Query([mine, lost])

        def commit(self):
            self.commits += 1

    assert mark_orphaned_builds(DB(), still_running=lambda vid, status: vid == mine.id) == 1
    assert mine.status == "building"
    assert lost.status == "failed"


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
            async def read(self, n):
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


def test_built_architectures_are_read_from_verbose_output():
    from app.api.jupyter_venvs import built_architectures

    lines = [
        'ok: [tkamd1 -> localhost] => {"changed": false, "log": "=== Building venv: agent-dev (amd64) ===\\nArchitecture marker written: amd64\\n=== Build completed successfully ===", "started": "2026-09-13T07:06:21Z"}',
        "                    echo \"Architecture marker written: $ARCH_NAME\"",
        'ok: [tkamd1 -> localhost] => {"log": "Architecture marker written: arm64\\n", "started": "2026-09-13T07:10:21Z"}',
    ]
    assert built_architectures(lines) == ["amd64", "arm64"]
    assert built_architectures([]) == []


def test_a_line_of_any_length_is_read_without_losing_the_build():
    import io
    from app.api.jupyter_venvs import read_process_output

    huge = "x" * (2 * 1024 * 1024)
    payload = ("first line\n" + "Architecture marker written: amd64\n" + huge + "\nlast").encode()

    class Stream:
        def __init__(self, data):
            self.data = data

        async def read(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

    log = io.StringIO()
    lines = asyncio.run(read_process_output(Stream(payload), log))
    assert lines[0] == "first line" and lines[1] == "Architecture marker written: amd64"
    assert len(lines[2]) == 2 * 1024 * 1024 and lines[3] == "last"
    assert log.getvalue() == payload.decode()


def test_the_venv_answer_carries_the_architectures_built():
    from app.api.jupyter_venvs import VenvResponse

    answer = VenvResponse(
        id="x", name="agent-dev", packages=["a"], status="success", output=None, is_template=True,
        parent_template_id=None, venv_path="/var/lib/jupyterhub-venvs/<arch>/agent-dev", architecture="amd64",
        architectures_built=["amd64", "arm64"], created_at="t", started_at=None, completed_at=None,
        created_by="system", duration=None,
    )
    assert answer.architectures_built == ["amd64", "arm64"]
    assert VenvResponse(**{**answer.model_dump(), "architectures_built": []}).architectures_built == []


def test_delete_answers_at_once_and_removes_from_every_node(monkeypatch):
    venv = SimpleNamespace(id=uuid.uuid4(), name="mine", status="success", output="ok", is_template=False, completed_at=None)
    db = FakeDB(venv)
    removed = []

    async def fake_delete(venv_id):
        removed.append(venv_id)

    monkeypatch.setattr(jv, "_execute_venv_delete", fake_delete)

    async def scenario():
        answer = await jv.delete_jupyter_venv(venv.id, db=db, current_user={})
        assert answer["status"] == "deleting"
        assert venv.status == "deleting"
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert removed == [str(venv.id)]


def test_delete_refuses_a_template_and_a_building_venv():
    from fastapi import HTTPException

    template = SimpleNamespace(id=uuid.uuid4(), name="agent-dev", status="success", is_template=True)
    building = SimpleNamespace(id=uuid.uuid4(), name="mine", status="building", is_template=False)
    for venv in (template, building):
        try:
            asyncio.run(jv.delete_jupyter_venv(venv.id, db=FakeDB(venv), current_user={}))
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError("expected a refusal")


def test_a_removal_orphaned_by_a_restart_is_marked_delete_failed():
    from app.db.init_venvs import mark_orphaned_builds

    removing = SimpleNamespace(id="33333333-3333-3333-3333-333333333333", name="gone", status="deleting", output=None, completed_at=None)

    class Query:
        def filter(self, *args):
            return self

        def all(self):
            return [removing]

    class DB:
        def query(self, model):
            return Query()

        def commit(self):
            pass

    assert mark_orphaned_builds(DB(), still_running=lambda vid, status: False) == 1
    assert removing.status == "delete_failed" and "delete it again" in removing.output


class FakeVenvQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def filter_by(self, **kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0]


class FakeVenvDB:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.commits = 0

    def query(self, model):
        return FakeVenvQuery(self.rows)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def custom_venv(name, archs):
    return SimpleNamespace(id=uuid.uuid4(), name=name, packages=["numpy"], status="success", is_template=False,
                           architectures_built=list(archs), output=None, started_at=None, completed_at=None)


def test_only_custom_venvs_missing_the_architecture_are_built(monkeypatch):
    import app.services.node_manager as nm
    monkeypatch.setattr(nm.node_manager, "get_cluster_architectures", lambda: ["amd64", "arm64", "riscv64"])
    old = custom_venv("data-tools", ["amd64", "arm64"])
    new = custom_venv("vision", ["amd64"])
    started = []

    async def fake_build(venv_id, architecture):
        started.append((venv_id, architecture))

    monkeypatch.setattr(jv, "_execute_venv_architecture_build", fake_build)

    async def scenario():
        answer = await jv.build_venvs_for_architecture(jv.ArchitectureBuildRequest(architecture="arm64"),
                                                       db=FakeVenvDB(old, new), current_user={})
        assert detached.running(f"venv-build:{new.id}")
        await asyncio.sleep(0)
        return answer

    answer = asyncio.run(scenario())
    assert answer == {"architecture": "arm64", "building": ["vision"], "already_built": ["data-tools"]}
    assert started == [(str(new.id), "arm64")]
    assert new.status == "building" and old.status == "success"


def test_an_architecture_the_cluster_lacks_is_refused(monkeypatch):
    import pytest
    from fastapi import HTTPException
    import app.services.node_manager as nm
    monkeypatch.setattr(nm.node_manager, "get_cluster_architectures", lambda: ["amd64", "arm64"])

    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await jv.build_venvs_for_architecture(jv.ArchitectureBuildRequest(architecture="riscv64"),
                                                  db=FakeVenvDB(), current_user={})
        assert refused.value.status_code == 400
        assert "riscv64" in refused.value.detail

    asyncio.run(scenario())


def _run_arch_build(monkeypatch, venv, return_code, lines):
    received = {}

    async def fake_playbook(v, playbook, extra_vars):
        received.update(extra_vars, playbook=playbook)
        return return_code, lines, "/tmp/thinkube-venvs/x/build-1.log"

    monkeypatch.setattr(jv, "_run_venv_playbook", fake_playbook)
    monkeypatch.setattr(jv, "SessionLocal", lambda: (lambda: FakeVenvDB(venv)))
    asyncio.run(jv._execute_venv_architecture_build(str(venv.id), "arm64"))
    return received


def test_a_build_for_one_architecture_adds_it(monkeypatch):
    venv = custom_venv("vision", ["amd64"])
    venv.status = "building"
    received = _run_arch_build(monkeypatch, venv, 0, ["ok: Architecture marker written: arm64"])

    assert received["target_architecture"] == "arm64" and received["is_template"] is False
    assert venv.status == "success"
    assert venv.architectures_built == ["amd64", "arm64"]


def test_a_failed_build_for_one_architecture_says_which_and_keeps_the_others(monkeypatch):
    venv = custom_venv("vision", ["amd64"])
    venv.status = "building"
    _run_arch_build(monkeypatch, venv, 2, ["fatal: no node"])

    assert venv.status == "failed"
    assert venv.architectures_built == ["amd64"]
    assert "Build for arm64 failed" in venv.output and "amd64 are unchanged" in venv.output
