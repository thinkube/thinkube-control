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
    assert ("exec python3 -m thinkube_mill run demo/clean.py --in rows=data/rows.jsonl "
            "--out cleaned=data/out.jsonl --set pause=0.5 --set lang=en --limit 50 --run-id forge-01234567-2") in script


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
