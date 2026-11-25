# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The Kubernetes job of one start of a job."""

from app.models.jobs import Job
from app.services import pods


def job(**fields):
    base = dict(id="0d6cbb6a-a4e9-4005-a7fd-15fde698ed26", name="train", node="tkamd2",
                image="library/tk-jupyter-base:latest", command=["python", "-m", "meepsquared", "train", "job.yaml"],
                env={"HOME": "/home/thinkube", "NVIDIA_VISIBLE_DEVICES": "all"}, working_dir="/work",
                mounts=["models", "mlflow", "venvs"], gpus=2, memory_gb=60, cpu_cores=8, stop_seconds=600)
    return Job(**{**base, **fields})


def test_a_gpu_job_is_given_its_gpus_by_id_and_asks_kubernetes_for_none():
    made = pods.manifest(job(), 2, ["GPU-a", "GPU-b"])
    assert (made["metadata"]["name"], made["metadata"]["namespace"]) == ("job-0d6cbb6a-2", "scheduler-runs")
    spec = made["spec"]["template"]["spec"]
    container = spec["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    # The job's own NVIDIA_VISIBLE_DEVICES is replaced by the GPUs it was given
    assert env["NVIDIA_VISIBLE_DEVICES"] == "GPU-a,GPU-b" and env["HOME"] == "/home/thinkube"
    assert (env["SCHEDULER_JOB_ID"], env["SCHEDULER_START"]) == ("0d6cbb6a-a4e9-4005-a7fd-15fde698ed26", "2")
    assert spec["runtimeClassName"] == "nvidia-legacy"
    # All its memory kept, half its cores; it uses all of them while the node has them free
    assert container["resources"] == {"requests": {"cpu": "4000m", "memory": "60Gi"},
                                      "limits": {"cpu": "8", "memory": "60Gi"}}
    assert container["image"] == "registry.test.com/library/tk-jupyter-base:latest"
    assert (container["command"][0], container["workingDir"]) == ("python", "/work")
    assert spec["nodeSelector"] == {"kubernetes.io/hostname": "tkamd2"}
    assert (spec["terminationGracePeriodSeconds"], spec["restartPolicy"]) == (600, "Never")
    assert spec["securityContext"] == {"runAsUser": 1000, "runAsGroup": 100}
    assert spec["serviceAccountName"] == "scheduler-job"
    assert made["spec"]["backoffLimit"] == 0


def test_a_job_mounts_the_folders_it_names_and_shared_memory():
    spec = pods.manifest(job(), 1, ["GPU-a"])["spec"]["template"]["spec"]
    mounts = {m["name"]: (m["mountPath"], m.get("readOnly")) for m in spec["containers"][0]["volumeMounts"]}
    assert mounts == {"shm": ("/dev/shm", None), "models": ("/home/thinkube/thinkube/models", False),
                      "mlflow": ("/home/thinkube/thinkube/mlflow", False),
                      "venvs": ("/var/lib/jupyterhub-venvs", True)}
    volumes = {v["name"]: v for v in spec["volumes"]}
    assert volumes["shm"]["emptyDir"] == {"medium": "Memory"}
    assert volumes["models"]["persistentVolumeClaim"] == {"claimName": "models", "readOnly": False}
    assert volumes["mlflow"]["persistentVolumeClaim"] == {"claimName": "mlflow-root", "readOnly": False}
    # A process that fails says why in its last lines
    assert spec["containers"][0]["terminationMessagePolicy"] == "FallbackToLogsOnError"
    assert volumes["venvs"]["hostPath"] == {"path": "/var/lib/jupyterhub-venvs", "type": "Directory"}


def test_a_job_without_a_gpu_sees_none_whatever_its_image_says():
    spec = pods.manifest(job(gpus=0, working_dir=None, mounts=[]), 1, [])["spec"]["template"]["spec"]
    env = {e["name"]: e["value"] for e in spec["containers"][0]["env"]}
    assert env["NVIDIA_VISIBLE_DEVICES"] == "void"
    assert "runtimeClassName" not in spec and "workingDir" not in spec["containers"][0]
