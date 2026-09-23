# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Adding nodes answers with a job id, runs on the server, and keeps the password out of every address."""

import asyncio

import pytest
from fastapi import HTTPException

import app.api.nodes as nodes
from app.api.nodes import AddNodesBatchRequest, AddNodesJob
from app.services import detached


class FakeNodeManager:
    def validate_inventory(self):
        return {"valid": True}

    def read_inventory(self):
        return {"all": {"vars": {
            "network_mode": "local",
            "overlay_provider": "tailscale",
            "tailscale_auth_key": "k",
            "tailscale_api_token": "t",
        }}}

    def get_cluster_nodes(self):
        return [{"name": "tkspark"}]


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setattr(nodes, "node_manager", FakeNodeManager())
    monkeypatch.setattr(nodes, "_add_node_playbooks", lambda provider: [])
    monkeypatch.setattr(nodes, "_add_node_jobs", {})
    monkeypatch.delenv("ANSIBLE_BECOME_PASSWORD", raising=False)


def request(password=None):
    return AddNodesBatchRequest(nodes=[{"ip": "10.0.0.9", "hostname": "worker1"}], password=password)


def test_the_run_starts_on_the_server_and_its_progress_is_polled(cluster, monkeypatch):
    run_may_finish = asyncio.Event()
    received = []

    async def fake_add_nodes(job, node_list, password):
        received.append((node_list, password))
        job.send({"type": "task", "task_name": "[worker1] Distribute SSH key", "task_number": 1})
        await run_may_finish.wait()
        job.send({"type": "complete", "status": "success", "message": "added"})

    monkeypatch.setattr(nodes, "_add_nodes", fake_add_nodes)

    async def scenario():
        answer = await nodes.add_nodes_batch(request(password="node-secret"), current_user={})
        job_id = answer["job_id"]
        assert answer["status"] == "running"
        await asyncio.sleep(0)
        assert detached.running(f"add-nodes:{job_id}")

        first = await nodes.get_add_nodes_job(job_id, after=0, current_user={})
        assert first["status"] == "running"
        assert [e["type"] for e in first["events"]] == ["task"]

        run_may_finish.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        rest = await nodes.get_add_nodes_job(job_id, after=first["next"], current_user={})
        assert rest["status"] == "success"
        assert [e["type"] for e in rest["events"]] == ["complete"]
        return job_id

    job_id = asyncio.run(scenario())
    assert received == [([{"ip": "10.0.0.9", "hostname": "worker1"}], "node-secret")]
    # The password reaches the run and nothing a poll returns.
    assert "node-secret" not in repr(nodes._add_node_jobs[job_id].events)


def test_without_a_password_the_cluster_password_is_used(cluster, monkeypatch):
    received = []

    async def fake_add_nodes(job, node_list, password):
        received.append(password)
        job.end()

    monkeypatch.setattr(nodes, "_add_nodes", fake_add_nodes)
    monkeypatch.setenv("ANSIBLE_BECOME_PASSWORD", "cluster-secret")

    async def scenario():
        await nodes.add_nodes_batch(request(), current_user={})
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert received == ["cluster-secret"]


def test_a_second_run_is_refused_while_one_is_running(cluster, monkeypatch):
    nodes._add_node_jobs["busy"] = AddNodesJob("busy", 1)
    started = []

    async def fake_add_nodes(job, node_list, password):
        started.append(job.id)

    monkeypatch.setattr(nodes, "_add_nodes", fake_add_nodes)

    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await nodes.add_nodes_batch(request(), current_user={})
        assert refused.value.status_code == 409

    asyncio.run(scenario())
    assert started == []


def test_a_missing_playbook_stops_the_run_before_it_starts(cluster, monkeypatch, tmp_path):
    missing = tmp_path / "20_join_workers.yaml"
    monkeypatch.setattr(nodes, "_add_node_playbooks", lambda provider: [missing])

    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await nodes.add_nodes_batch(request(), current_user={})
        assert refused.value.status_code == 500
        assert str(missing) in refused.value.detail

    asyncio.run(scenario())
    assert nodes._add_node_jobs == {}


def test_an_unknown_job_says_why():
    async def scenario():
        with pytest.raises(HTTPException) as unknown:
            await nodes.get_add_nodes_job("nope", after=0, current_user={})
        assert unknown.value.status_code == 404
        assert "restart forgets them" in unknown.value.detail

    asyncio.run(scenario())


def test_a_run_that_stops_on_an_error_ends_as_failed_with_that_error():
    job = AddNodesJob("j", 1)
    job.send({"type": "error", "message": "Inventory validation failed: bad group"})
    job.end()

    assert job.status == "failed"
    assert job.events[-1] == {
        "type": "complete",
        "status": "failed",
        "message": "Adding nodes failed: Inventory validation failed: bad group",
    }


def test_a_run_that_completed_is_left_as_it_ended():
    job = AddNodesJob("j", 1)
    job.send({"type": "complete", "status": "success", "message": "added"})
    job.end()

    assert job.status == "success"
    assert len(job.events) == 1
