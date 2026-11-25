# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A run's job as the Scheduler gets it: Forge's script with its parameters, in the Python environment it names,
on its node with its GPUs, priority and memory; and the Scheduler's job read back in Forge's words for a job."""

import json
from types import SimpleNamespace

import pytest

from app.services import control, runs, scheduled

ENV = {"MLFLOW_KEYCLOAK_TOKEN_URL": "https://auth/token", "MLFLOW_KEYCLOAK_CLIENT_ID": "mlflow",
       "MLFLOW_CLIENT_SECRET": "s", "MLFLOW_AUTH_USERNAME": "u", "MLFLOW_AUTH_PASSWORD": "p",
       "DOMAIN_NAME": "thinkube.com"}


@pytest.fixture
def env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def request(**fields):
    return {"name": "Forge r: train", "priority": 2, "resumable": True, "notebook": runs.notebook(),
            "parameters": {"run_id": "r1", "mode": "train"}, "kernel_name": "meepsquared", "node": "tkamd2",
            "gpus": 2, "memory_gb": 64, "cpu_cores": 16, **fields}


def test_a_job_runs_forges_script_with_its_parameters_in_its_environment(env):
    spec = scheduled.job_spec(request(), "tok")
    assert (spec["name"], spec["owner"], spec["node"], spec["gpus"], spec["gpu_memory_gb"]) == (
        "Forge r: train", "forge", "tkamd2", 2, None)
    assert (spec["memory_gb"], spec["cpu_cores"], spec["priority"], spec["resumable"]) == (64, 16, 2, True)
    assert spec["mounts"] == ["models", "mlflow", "venvs"] and spec["image"] == "library/tk-jupyter-base:latest"
    assert spec["command"][:2] == ["sh", "-c"]
    assert '/var/lib/jupyterhub-venvs/custom/meepsquared/"$(uname -m' in spec["command"][2]
    assert spec["command"][2].endswith('/bin/python3 -u -c "$FORGE_SCRIPT"')
    env = spec["env"]
    assert json.loads(env["FORGE_PARAMETERS"]) == {"run_id": "r1", "mode": "train"}
    assert env["FORGE_SCRIPT"].startswith("import json, os\nPARAMETERS = json.loads(os.environ['FORGE_PARAMETERS'])")
    # Every cell of the script, in order
    assert env["FORGE_SCRIPT"].index("# %% Run") < env["FORGE_SCRIPT"].index("# %% Register")
    assert (env["HOME"], env["PYTHONPATH"], env["THINKUBE_API_TOKEN"]) == ("/home/thinkube", "/opt/thinkube", "tok")
    assert env["THINKUBE_CONTROL_URL"] == "https://control.thinkube.com"
    assert env["MLFLOW_CLIENT_SECRET"] == "s" and env["MLFLOW_TRACKING_URI"]


def test_the_platforms_own_environment_is_found_by_architecture_first(env):
    spec = scheduled.job_spec(request(kernel_name="fine-tuning", resumable=False), "tok")
    assert '/var/lib/jupyterhub-venvs/"$(uname -m' in spec["command"][2]
    assert '/fine-tuning/bin/python3' in spec["command"][2] and spec["resumable"] is False


def test_a_job_needs_the_settings_to_reach_mlflow(env, monkeypatch):
    monkeypatch.delenv("MLFLOW_CLIENT_SECRET")
    with pytest.raises(control.ControlError, match="has no MLFLOW_CLIENT_SECRET"):
        scheduled.job_spec(request(), "tok")


def job(status, **fields):
    return {"id": "j1", "status": status, "node": "tkamd2", "gpus": 2, "waiting": None, "error": None, "starts": 1,
            "preempted_by": None, **fields}


def test_the_schedulers_states_in_forges_words():
    assert scheduled.view(job("waiting", waiting="waiting for 2 GPUs of tkamd2"))["status"] == "starting"
    assert scheduled.view(job("running"))["status"] == "running"
    assert scheduled.view(job("stopping"))["status"] == "running"
    assert scheduled.view(job("completed"))["status"] == "completed"
    assert scheduled.view(job("stopped"))["status"] == "cancelled"
    seen = scheduled.view(job("waiting", waiting="stopped to make room for job x; waiting for its turn"))
    assert seen["waiting"].startswith("stopped to make room") and seen["job_id"] == "j1"


def test_a_failed_job_says_the_exception_its_process_raised():
    log = ("the job's process exited with code 1: Traceback (most recent call last):\n"
           '  File "<string>", line 240, in <module>\n'
           "ValueError: method='fp8' needs FP8 tensor cores\n")
    failed = scheduled.view(job("failed", error=log))
    assert failed["status"] == "error" and failed["error"] == "ValueError: method='fp8' needs FP8 tensor cores"
    assert scheduled.raised("meepsquared.job.MeasureAgain: the rank changed") == \
        "meepsquared.job.MeasureAgain: the rank changed"
    assert scheduled.raised("the pod failed: Evicted: the node ran out of memory") == \
        "the pod failed: Evicted: the node ran out of memory"


def test_every_job_request_says_its_name_priority_and_whether_it_resumes():
    run = SimpleNamespace(id="r1", name="night", priority=2, preprocess={"folder": "/p/k"}, node="tkamd2",
                          memory_gb=32, cpu_cores=8, job={"gpus": 2}, base_model="b", dataset={}, milestones=None,
                          mlflow_run_id="m", mlflow_experiment_id="5", register=False, model_id=None, reuse=None)
    choices = {"cpu_cores": [4, 8, 16], "memory_gb": [16, 32, 64], "gpus": [0, 1, 2]}
    train = runs.job_request(run, None, "train", choices)
    assert (train["name"], train["priority"], train["resumable"]) == ("Forge night: train", 2, True)
    preprocess = runs.job_request(run, None, "preprocess", choices, node="tkamd1")
    assert (preprocess["name"], preprocess["resumable"], preprocess["gpus"]) == ("Forge night: preprocess", False, 0)
