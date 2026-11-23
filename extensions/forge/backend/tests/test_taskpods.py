# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The Kubernetes job of a task run, and what a pod says when it fails or waits."""

from types import SimpleNamespace as NS

from app.models.forge import Run
from app.services import taskpods


def task_run(**task):
    return Run(id="0123456789abcdef", name="clean", kind="task", node="tkspark", memory_gb=8, cpu_cores=2,
               task={"file": "demo/clean.py", "task": None, "inputs": {"rows": "data/rows.jsonl"},
                     "outputs": {"cleaned": "data/out.jsonl"}, "settings": {"pause": 0.5, "lang": "en"},
                     "limit": 50, "gpus": 1, **task})


def test_the_job_runs_mill_on_the_runs_node_with_its_resources():
    job = taskpods.job_manifest(task_run(), 2)
    assert job["metadata"]["name"] == "forge-task-01234567-2"
    assert job["metadata"]["namespace"] == "mill-runs"
    assert job["spec"]["backoffLimit"] == 0
    pod = job["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "tkspark"}
    assert pod["securityContext"] == {"runAsUser": 1000, "runAsGroup": 100}
    assert pod["tolerations"] == [{"key": "thinkube.io/training", "operator": "Equal", "value": "true",
                                   "effect": "NoSchedule"}]
    [container] = pod["containers"]
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"}
    assert container["resources"]["requests"] == container["resources"]["limits"]
    assert container["workingDir"] == "/storage/thinkube-shared/mill"
    assert pod["volumes"] == [{"name": "shared", "persistentVolumeClaim": {"claimName": "thinkube-shared"}}]
    assert container["volumeMounts"] == [{"name": "shared", "mountPath": "/storage/thinkube-shared"}]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MILL_RUNNER"] == "forge" and env["MILL_SUBJECTS"] == "mill"
    assert env["PIP_INDEX_URL"].endswith("/root/stable/+simple/")
    script = container["command"][2]
    assert "pip install" in script and "thinkube-mill" in script
    assert ("python3 -m thinkube_mill run demo/clean.py --in rows=data/rows.jsonl "
            "--out cleaned=data/out.jsonl --set pause=0.5 --set lang=en --limit 50 --run-id forge-01234567-2 &") in script
    assert container["image"].endswith("/library/ai-inference-base:cuda13.0-torch2.9-py3.12")


def test_the_task_is_given_the_gateway_and_the_queues_token():
    def env(job):
        return {e["name"]: e.get("value") for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}

    without = env(taskpods.job_manifest(task_run(), 1))
    assert without["LLM_GATEWAY_URL"] == "http://llm-proxy.thinkube-control.svc.cluster.local:8080"
    assert "THINKUBE_API_TOKEN" not in without
    assert env(taskpods.job_manifest(task_run(), 1, "tk_of_the_queue"))["THINKUBE_API_TOKEN"] == "tk_of_the_queue"


def test_a_task_without_gpus_asks_for_none_and_a_named_task_is_targeted():
    job = taskpods.job_manifest(task_run(gpus=0, task="score"), 1)
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert "nvidia.com/gpu" not in container["resources"]["limits"]
    assert "run demo/clean.py:score " in container["command"][2]


def test_a_path_with_spaces_stays_one_argument():
    job = taskpods.job_manifest(task_run(inputs={"rows": "data/my rows.jsonl"}), 1)
    assert "--in 'rows=data/my rows.jsonl'" in job["spec"]["template"]["spec"]["containers"][0]["command"][2]


def env_of(job):
    return {e["name"]: e.get("value") for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_every_start_of_a_run_has_the_same_state_folder_and_says_which_start_it_is():
    first, second = env_of(taskpods.job_manifest(task_run(), 1)), env_of(taskpods.job_manifest(task_run(), 2))
    assert first["FORGE_STATE_DIR"] == second["FORGE_STATE_DIR"] == \
        "/storage/thinkube-shared/forge-runs/0123456789abcdef/state"
    assert (first["FORGE_ATTEMPT"], second["FORGE_ATTEMPT"]) == ("1", "2")
    assert first["FORGE_RUN_ID"] == "0123456789abcdef"


def test_the_state_folder_goes_only_when_the_task_ends_well_and_a_stop_reaches_the_task():
    script = taskpods.job_manifest(task_run(), 1)["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert 'mkdir -p "$FORGE_STATE_DIR"' in script
    assert 'if [ "$status" -eq 0 ]; then rm -rf "$FORGE_STATE_DIR"; fi' in script
    assert "trap 'kill -TERM \"$child\" 2>/dev/null' TERM" in script
    assert "exec " not in script


def run_script(tmp_path, task_line: str, signal_after=None):
    """The pod's script with the task replaced by task_line, run by sh; returns (exit code, state folder kept)."""
    import os
    import signal
    import subprocess
    import time

    script = taskpods.script(task_run(), "forge-x-1")
    lines = script.splitlines()
    lines = [task_line + " &" if line.endswith(" &") else line for line in lines]
    lines = [line for line in lines if "pip install" not in line]
    state = tmp_path / "state"
    process = subprocess.Popen(["sh", "-c", "\n".join(lines)], env={**os.environ, "FORGE_STATE_DIR": str(state)})
    if signal_after is not None:
        time.sleep(signal_after)
        process.send_signal(signal.SIGTERM)
    return process.wait(timeout=30), state.exists()


def test_a_task_that_ends_well_leaves_no_state(tmp_path):
    assert run_script(tmp_path, "true") == (0, False)


def test_a_failed_task_keeps_its_state(tmp_path):
    assert run_script(tmp_path, "false") == (1, True)


def test_a_stopped_task_gets_the_signal_ends_and_keeps_its_state(tmp_path):
    # The task traps SIGTERM, saves, and ends with a code of its own: the shell waits for it and exits with it
    task = "sh -c 'trap \"exit 7\" TERM; while true; do sleep 0.1; done'"
    assert run_script(tmp_path, task, signal_after=1.0) == (7, True)


def test_a_task_in_a_venv_runs_on_the_notebook_image_with_the_nodes_venvs():
    job = taskpods.job_manifest(task_run(venv="meepsquared"), 1)
    pod = job["spec"]["template"]["spec"]
    [container] = pod["containers"]
    assert container["image"].endswith("/library/tk-jupyter-base:latest")
    assert {"name": "venvs", "mountPath": "/home/thinkube/venvs", "readOnly": True} in container["volumeMounts"]
    assert {"name": "venvs", "hostPath": {"path": "/var/lib/jupyterhub-venvs", "type": "Directory"}} in pod["volumes"]
    script = container["command"][2]
    assert 'PYTHON="/home/thinkube/venvs/custom/meepsquared/$ARCH/bin/python"' in script
    assert '"$PYTHON" -m pip install -q --no-deps --target /tmp/thinkube-mill thinkube-mill' in script
    assert '"$PYTHON" -m thinkube_mill run demo/clean.py' in script
    assert "--user" not in script


def test_a_task_without_a_venv_or_models_mounts_only_the_shared_folder():
    pod = taskpods.job_manifest(task_run(), 1)["spec"]["template"]["spec"]
    assert [v["name"] for v in pod["volumes"]] == ["shared"]
    assert "FORGE_MODELS" not in env_of(taskpods.job_manifest(task_run(), 1))


def test_a_task_with_models_reads_their_folders_on_mlflows_file_system():
    import json

    job = taskpods.job_manifest(task_run(models={"google/gemma-4-E4B-it": "artifacts/3/abc/artifacts/model"}), 1)
    assert json.loads(env_of(job)["FORGE_MODELS"]) == {
        "google/gemma-4-E4B-it": "/home/thinkube/thinkube/mlflow/artifacts/3/abc/artifacts/model"}
    pod = job["spec"]["template"]["spec"]
    assert {"name": "models", "mountPath": "/home/thinkube/thinkube/mlflow", "readOnly": True} in \
        pod["containers"][0]["volumeMounts"]
    assert {"name": "models", "persistentVolumeClaim": {"claimName": "mlflow-root", "readOnly": True}} in pod["volumes"]


def pod(phase="Failed", terminated=None, waiting=None, conditions=()):
    state = NS(terminated=terminated, waiting=waiting)
    return NS(status=NS(phase=phase, reason=None, message=None, conditions=list(conditions),
                        container_statuses=[NS(state=state)]))


def test_a_failed_pod_says_its_exit():
    ended = NS(exit_code=137, reason="OOMKilled", message=None)
    assert taskpods._exit(pod(terminated=ended)) == "the task's process exited with code 137 (OOMKilled)"
    assert taskpods._exit(None) == "the job failed and its pod is gone"


def test_a_pending_pod_says_why_it_waits():
    unplaced = NS(type="PodScheduled", status="False", reason="Unschedulable",
                  message="0/3 nodes are available: 1 Insufficient nvidia.com/gpu")
    assert taskpods._waiting(pod("Pending", conditions=[unplaced])) == \
        "the pod waits for room on its node: 0/3 nodes are available: 1 Insufficient nvidia.com/gpu"
    pulling = NS(reason="ImagePullBackOff", message="not found")
    assert taskpods._waiting(pod("Pending", waiting=pulling)) == "the pod cannot start: ImagePullBackOff: not found"
    assert taskpods._waiting(pod("Running")) is None
