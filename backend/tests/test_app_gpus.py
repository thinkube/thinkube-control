# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The apps' GPUs: Control asks the Scheduler for a hold for each app Deployment that declares GPUs, writes the
ids of the GPUs it is given where the pod reads them, stops the app's pods when the Scheduler asks for the GPUs
back, and ends the holds of Deployments that are gone."""

from types import SimpleNamespace as NS

from app.services import app_gpus
from app.services.k8s_manager import pod_gpus


def deployment(name="sd-inference", namespace="sd", replicas=1, node="tkamd2", generation=1, memory="16Gi"):
    return NS(
        metadata=NS(name=name, namespace=namespace, generation=generation,
                    annotations={app_gpus.GPUS_ANNOTATION: "1", app_gpus.GPU_MEMORY_ANNOTATION: "20"}),
        spec=NS(replicas=replicas, selector=NS(match_labels={"app.kubernetes.io/component": "inference"}),
                template=NS(spec=NS(node_selector={"kubernetes.io/hostname": node} if node else None,
                                    containers=[NS(resources=NS(limits={"memory": memory}))]))))


def pod(name, running):
    state = NS(running=NS() if running else None)
    return NS(metadata=NS(name=name, namespace="sd", deletion_timestamp=None),
              status=NS(phase="Running" if running else "Pending", container_statuses=[NS(state=state)]))


class Cluster:
    def __init__(self, deployments):
        self.items, self.maps, self.pod_list, self.deleted = deployments, {}, [], []

    def deployments(self):
        return self.items

    def configmap(self, namespace, name):
        found = self.maps.get((namespace, name))
        return None if found is None else NS(metadata=NS(annotations=dict(found["annotations"])),
                                             data=dict(found["data"]))

    def write_configmap(self, namespace, name, annotations, ids):
        kept = dict(self.maps.get((namespace, name), {"annotations": {}})["annotations"])
        for key, value in annotations.items():
            if value is None:
                kept.pop(key, None)
            else:
                kept[key] = value
        self.maps[(namespace, name)] = {"annotations": kept, "data": {app_gpus.IDS_KEY: ids} if ids else {}}

    def pods(self, deployment):
        return list(self.pod_list)

    def delete_pod(self, p):
        self.deleted.append(p.metadata.name)
        self.pod_list = [q for q in self.pod_list if q is not p]

    def ids(self):
        return self.maps[("sd", "sd-inference-gpus")]["data"].get(app_gpus.IDS_KEY)

    def hold(self):
        return self.maps[("sd", "sd-inference-gpus")]["annotations"].get(app_gpus.HOLD_ANNOTATION)


class Scheduler:
    def __init__(self):
        self.holds, self.asked, self.ended, self.released = {}, [], [], []

    def priority(self, kind):
        return {"app": 5}[kind]

    def open_holds(self):
        return [h for h in self.holds.values() if h["status"] in app_gpus.OPEN]

    def get(self, hold_id):
        return self.holds[hold_id]

    def ask(self, request):
        made = {"id": f"h{len(self.asked) + 1}", "status": "waiting", "gpu_ids": None, **request}
        self.holds[made["id"]] = made
        self.asked.append(request)
        return made

    def end(self, hold_id):
        self.holds[hold_id]["status"] = "stopped"
        self.ended.append(hold_id)

    def release(self, hold_id):
        self.holds[hold_id].update(status="waiting", gpu_ids=None)
        self.released.append(hold_id)


def world(*deployments):
    cluster, scheduler = Cluster(list(deployments) or [deployment()]), Scheduler()
    return cluster, scheduler, app_gpus.AppGpus(cluster, scheduler)


def test_an_app_asks_for_its_gpus_and_its_pod_starts_on_the_ones_it_is_given():
    cluster, scheduler, worker = world()
    cluster.pod_list = [pod("waiting", running=False)]
    worker.tick()
    assert scheduler.asked == [{"name": "sd/sd-inference", "owner": "thinkube-control", "node": "tkamd2", "gpus": 1,
                                "gpu_memory_gb": 20.0, "memory_gb": 16, "priority": 5,
                                "release_url": app_gpus.RELEASE_URL}]
    assert cluster.hold() == "h1" and cluster.ids() is None and cluster.deleted == []
    scheduler.holds["h1"].update(status="running", gpu_ids=["GPU-1"])
    worker.tick()
    # The ids are written, and the pod that waited for them is started again
    assert cluster.ids() == "GPU-1" and cluster.deleted == ["waiting"]
    cluster.pod_list = [pod("started", running=True)]
    worker.tick()
    assert cluster.deleted == ["waiting"] and len(scheduler.asked) == 1


def test_asked_for_its_gpus_an_app_stops_and_releases_them_once_its_pods_are_gone():
    cluster, scheduler, worker = world()
    worker.tick()
    scheduler.holds["h1"].update(status="running", gpu_ids=["GPU-1"])
    worker.tick()
    cluster.pod_list = [pod("serving", running=True)]
    scheduler.holds["h1"]["status"] = "stopping"
    terminating = pod("serving", running=True)
    terminating.metadata.deletion_timestamp = "now"

    def delete(p):
        cluster.deleted.append(p.metadata.name)
        cluster.pod_list = [terminating]

    cluster.delete_pod = delete
    worker.tick()
    # The ids are taken out and the pod is stopped; it still runs while it ends, so the hold is not released yet
    assert cluster.ids() is None and cluster.deleted == ["serving"] and scheduler.released == []
    cluster.pod_list = [pod("again", running=False)]
    worker.tick()
    assert scheduler.released == ["h1"] and scheduler.holds["h1"]["status"] == "waiting"
    # Given GPUs again, the app starts on them
    scheduler.holds["h1"].update(status="running", gpu_ids=["GPU-0"])
    worker.tick()
    assert cluster.ids() == "GPU-0" and "again" in cluster.deleted and len(scheduler.asked) == 1


def test_a_hold_ends_when_its_deployment_has_no_replicas_or_is_gone():
    cluster, scheduler, worker = world()
    worker.tick()
    scheduler.holds["h1"].update(status="running", gpu_ids=["GPU-1"])
    worker.tick()
    cluster.items = [deployment(replicas=0)]
    worker.tick()
    assert scheduler.ended == ["h1"] and cluster.ids() is None and cluster.hold() is None
    cluster.items = [deployment()]
    worker.tick()
    assert cluster.hold() == "h2"
    cluster.items = []
    worker.tick()
    assert scheduler.ended == ["h1", "h2"]


def test_a_hold_its_node_can_never_run_is_asked_again_only_once_the_deployment_changed():
    cluster, scheduler, worker = world()
    worker.tick()
    scheduler.holds["h1"].update(status="failed", error="tkamd2 has 2 GPUs, and the job asks for 3")
    worker.tick()
    worker.tick()
    assert len(scheduler.asked) == 1
    cluster.items = [deployment(generation=2)]
    worker.tick()
    assert len(scheduler.asked) == 2 and cluster.hold() == "h2"


def test_a_deployment_without_a_node_holds_no_gpus_and_says_why():
    cluster, scheduler, worker = world()
    worker.tick()
    cluster.items = [deployment(node=None)]
    worker.tick()
    assert scheduler.ended == ["h1"]
    assert "names no node" in cluster.maps[("sd", "sd-inference-gpus")]["annotations"][app_gpus.ERROR_ANNOTATION]


def test_the_memory_limit_is_counted_in_whole_gib():
    assert app_gpus.memory_limit_gb(deployment(memory="16Gi")) == 16
    assert app_gpus.memory_limit_gb(deployment(memory="1500Mi")) == 2
    assert app_gpus.memory_limit_gb(deployment(memory="8589934592")) == 8


def k8s_pod(limits=None, runtime=None, env=None, annotations=None, running=True):
    container = NS(resources=NS(limits=limits) if limits else None, env=env or [])
    return NS(spec=NS(containers=[container], runtime_class_name=runtime),
              metadata=NS(annotations=annotations or {}),
              status=NS(container_statuses=[NS(state=NS(running=NS() if running else None))]))


def test_a_pods_gpus_are_counted_by_what_it_asks_kubernetes_or_the_ids_it_is_given():
    assert pod_gpus(k8s_pod(limits={"nvidia.com/gpu": "2"})) == 2
    ids = [NS(name="NVIDIA_VISIBLE_DEVICES", value="GPU-a,GPU-b")]
    assert pod_gpus(k8s_pod(runtime="nvidia-legacy", env=ids)) == 2
    assert pod_gpus(k8s_pod(runtime="nvidia-legacy", env=[NS(name="NVIDIA_VISIBLE_DEVICES", value="void")])) == 0
    from_map = [NS(name="NVIDIA_VISIBLE_DEVICES", value=None)]
    annotated = {"scheduler.thinkube.io/gpus": "1"}
    assert pod_gpus(k8s_pod(runtime="nvidia-legacy", env=from_map, annotations=annotated)) == 1
    # An app's pod that waits for its GPU ids holds none
    assert pod_gpus(k8s_pod(runtime="nvidia-legacy", env=from_map, annotations=annotated, running=False)) == 0
    assert pod_gpus(k8s_pod()) == 0
