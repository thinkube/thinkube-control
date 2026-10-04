# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Secrets never leave a playbook run: every runner blanks them before any line goes out."""

import asyncio

import app.api.nodes as nodes
import app.api.websocket_executor as websocket_executor
from app.services.scrub import MASK, Scrubber

BECOME = "s3cret-become-pw"
TOKEN = "ghp_abcdefghijklmnop123456"

# What a verbose Ansible run can print about its own variables.
LEAKY_OUTPUT = [
    "TASK [Log in to the registry]",
    f"ok: [node1] => {{\"ansible_become_pass\": \"{BECOME}\"}}",
    f"changed: [node1] => cmd: git clone https://thinkube:{TOKEN}@github.com/x/y",
    f"fatal: [node1]: FAILED! => {{\"msg\": \"login failed for {TOKEN}\"}}",
    "PLAY RECAP",
]


def test_the_values_given_to_a_run_are_blanked_wherever_they_appear():
    scrub = Scrubber.for_run({"ansible_become_pass": BECOME, "domain_name": "example.com"}, {"GITHUB_TOKEN": TOKEN})
    line = scrub.clean(f"echo {BECOME} and {TOKEN} for example.com")
    assert BECOME not in line and TOKEN not in line
    assert "example.com" in line


def test_credentials_are_blanked_by_the_name_beside_them():
    scrub = Scrubber()
    assert scrub.clean("password: hunter22") == f"password: {MASK}"
    assert scrub.clean('{"api_token": "abc123xyz"}') == f'{{"api_token": "{MASK}"}}'
    assert scrub.clean("Authorization: Bearer eyJhbGciOi") == f"Authorization: Bearer {MASK}"
    assert scrub.clean("https://user:pa55word@host/x") == f"https://user:{MASK}@host/x"


class FakeStdout:
    def __init__(self, lines):
        self._lines = [line.encode() + b"\n" for line in lines]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class FakeProcess:
    def __init__(self, lines):
        self.stdout = FakeStdout(lines)
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0

    def terminate(self):
        pass


def fake_ansible(monkeypatch, module):
    async def create_subprocess_exec(*args, **kwargs):
        return FakeProcess(LEAKY_OUTPUT)

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(module.ansible_env, "get_inventory_path", lambda: "/tmp/inventory.yaml")
    monkeypatch.setattr(module.ansible_env, "get_environment", lambda context="template": {"GITHUB_TOKEN": TOKEN})


def assert_no_secret(texts):
    for text in texts:
        assert BECOME not in text and TOKEN not in text, text


def test_adding_nodes_records_no_secret(monkeypatch, tmp_path):
    fake_ansible(monkeypatch, nodes)
    playbook = tmp_path / "20_join_workers.yaml"
    playbook.write_text("- hosts: all\n")
    job = nodes.AddNodesJob("j", 1)

    asyncio.run(nodes._stream_playbook(job, playbook, {"ansible_become_pass": BECOME}, "Join", 1))

    assert_no_secret(str(event) for event in job.events)
    assert any(MASK in str(event) for event in job.events)


def test_the_playbook_socket_sends_no_secret(monkeypatch, tmp_path):
    fake_ansible(monkeypatch, websocket_executor)
    playbook = tmp_path / "10_deploy.yaml"
    playbook.write_text("- hosts: all\n")
    monkeypatch.setattr(websocket_executor.ansible_env, "validate_paths", lambda: {"valid": True, "errors": []})
    monkeypatch.setattr(websocket_executor.ansible_env, "prepare_auth_vars", lambda v: {**v, "ansible_become_pass": BECOME})
    sent = []

    class FakeSocket:
        async def send_json(self, data):
            sent.append(data)

    asyncio.run(websocket_executor._execute_playbook(FakeSocket(), str(playbook), {}, {}))

    assert_no_secret(str(message) for message in sent)
    assert any(MASK in str(message) for message in sent)
