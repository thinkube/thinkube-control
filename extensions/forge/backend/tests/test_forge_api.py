# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Forge's API with Thinkube Control, MLflow and the Datasets extension
replaced: a run is a Meep² job on a dataset of Datasets, planned and queued
by the worker; the queue is paused, resumed, cancelled and reordered; slots
are defined; the worker gets its Control token."""

import pytest

from app.core.security import get_current_user
from app.models.forge import Run, WorkerCredential
from app.services import control, datasets, mlflow, runfiles, worker

MODELS = [
    {"id": "BSC-LT/salamandraTA-2b-instruct", "name": "SalamandraTA 2B", "params_b": 2.25, "context_length": 8192,
     "quantization": "BF16", "task": "text-generation", "is_downloaded": True, "is_finetuned": False,
     "server_type": ["vllm"]},
    {"id": "nvidia/Qwen3.8-27B-NVFP4", "name": "Qwen", "params_b": 27, "context_length": 262144,
     "quantization": "NVFP4", "task": "text-generation", "is_downloaded": True, "is_finetuned": False,
     "server_type": ["vllm"]},
]


class Control:
    def __init__(self):
        self.requests = []
        self.job_state = {"job_id": "j1", "status": "starting", "error": None}
        self.cancelled = []
        self.venvs = [{"name": "meepsquared", "status": "success"}]
        self.tokens = []

    async def catalog(self, token):
        return {"models": MODELS}

    async def list_venvs(self, token):
        return self.venvs

    async def cancel_job(self, token, job_id):
        self.cancelled.append(job_id)
        return {**self.job_state, "job_id": job_id, "status": "cancelled"}

    async def create_api_token(self, token, name):
        self.tokens.append(name)
        return {"id": "t1", "name": name, "token": "tk_worker"}

    async def revoke_api_token(self, token, token_id):
        self.tokens.remove("Forge training queue")
        return {"message": "revoked"}

    async def run_job(self, token, request):
        self.requests.append(request)
        return dict(self.job_state)

    async def job(self, token, job_id):
        return dict(self.job_state)


TRAINING = {
    "id": "d1", "name": "greetings", "format": "messages",
    "mapping": {"kind": "prompt", "columns": {"prompt": "q", "completion": "a"}},
    "splits": [
        {"name": "train", "rows": 9, "bytes": 1, "path": "artifacts/3/r1/artifacts/data/train.parquet"},
        {"name": "test", "rows": 1, "bytes": 1, "path": "artifacts/3/r1/artifacts/data/test.parquet"},
    ],
}


class Datasets:
    """Dataset d1 has versions 1 and 2; 2 is its latest ready one."""

    def __init__(self):
        self.asked = []

    async def trainable(self, token):
        return [{"id": "d1", "name": "greetings", "version": 2, "status": "ready", "mapping": TRAINING["mapping"]}]

    async def versions(self, token, dataset_id):
        return [{"version": 2, "status": "ready", "mapping": TRAINING["mapping"]},
                {"version": 1, "status": "ready", "mapping": TRAINING["mapping"]}]

    async def training(self, token, dataset_id, version):
        if dataset_id != "d1":
            raise datasets.DatasetsError(404, f"dataset {dataset_id} not found")
        if version not in (None, 1, 2):
            raise datasets.DatasetsError(404, f"dataset greetings has no version {version}")
        return {**TRAINING, "version": version or 2}

    async def version(self, token, dataset_id, number):
        return {"version": number, "columns": [{"name": c, "type": "VARCHAR"} for c in ("q", "a", "direction")]}

    async def first_example(self, token, dataset_id, version, split):
        self.asked.append((dataset_id, version, split))
        return {"messages": [{"role": "user", "content": "Hi?"}, {"role": "assistant", "content": "Hello"}]}


class Files:
    def __init__(self):
        self.stops = []

    async def request_stop(self, run):
        self.stops.append(run.id)


@pytest.fixture
def api(client, mock_user, monkeypatch, test_db):
    fake = Control()
    for name in ("catalog", "run_job", "job", "cancel_job", "create_api_token", "revoke_api_token"):
        monkeypatch.setattr(control, name, getattr(fake, name))
    monkeypatch.setattr(control, "venvs", fake.list_venvs)
    created = []

    async def create(name, forge_run_id, dataset):
        created.append(forge_run_id)
        client.tags = {"datasets.id": dataset["id"], "datasets.version": dataset["version"]}
        return "5", f"m{len(created)}"

    monkeypatch.setattr(runfiles, "create", create)
    files = Files()
    monkeypatch.setattr(runfiles, "RunFiles", lambda: files)
    data = Datasets()
    for name in ("trainable", "versions", "training", "version", "first_example"):
        monkeypatch.setattr(datasets, name, getattr(data, name))

    async def user():
        return mock_user

    client.app.dependency_overrides[get_current_user] = user
    client.headers["Authorization"] = "Bearer token"
    client.fake, client.files, client.db, client.data = fake, files, test_db, data
    test_db.add(WorkerCredential(id=worker.CREDENTIAL_ID, token="tk_worker", token_id="t0", username="testuser"))
    test_db.commit()
    return client


RUN = {"name": "Greeter", "dataset_id": "d1", "base_model": "BSC-LT/salamandraTA-2b-instruct", "node": "tkspark",
       "steps": 100}


def put(api, run_id, **fields):
    """Set a run's state as the worker would."""
    run = api.db.get(Run, run_id)
    for k, v in fields.items():
        setattr(run, k, v)
    api.db.commit()


def test_only_mirrored_bf16_text_models_are_offered(api):
    assert [m["id"] for m in api.get("/api/v1/models").json()] == ["BSC-LT/salamandraTA-2b-instruct"]


def test_the_datasets_are_those_of_the_datasets_extension(api):
    assert [d["id"] for d in api.get("/api/v1/datasets").json()] == ["d1"]


def test_a_run_is_a_meepsquared_job_planned_on_its_node(api):
    r = api.post("/api/v1/runs", json={**RUN, "decisions": {"rank": 32, "micro_batch": 4}, "memory_gb": 48})
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["status"], run["model_id"], run["when"]) == ("preprocessing", "forge/greeter", "now")
    assert run["job_spec"] == {"task": "sft", "method": "lora", "report_to": "mlflow", "steps": 100,
                               "recipe": {"rank": 32}, "overrides": {"micro_batch": 4}}
    assert run["dataset"]["train"] == {"split": "train", "path": "artifacts/3/r1/artifacts/data/train.parquet", "rows": 9}
    assert run["mlflow_run_id"] == "m1" and run["memory_gb"] == 48
    # Forge starts no job itself: the worker does, when the node is free
    assert api.fake.requests == []


def test_a_run_needs_the_queue_connected(api):
    api.db.delete(api.db.get(WorkerCredential, worker.CREDENTIAL_ID))
    api.db.commit()
    r = api.post("/api/v1/runs", json=RUN)
    assert r.status_code == 409 and "Slots page" in r.json()["detail"]


def test_a_run_needs_the_meepsquared_environment(api):
    api.fake.venvs = [{"name": "meepsquared", "status": "building"}]
    r = api.post("/api/v1/runs", json=RUN)
    assert r.status_code == 422 and "meepsquared is not built" in r.json()["detail"]
    api.fake.venvs = []
    assert "does not exist" in api.post("/api/v1/runs", json=RUN).json()["detail"]


def test_steps_or_epochs_and_known_decisions_only(api):
    assert api.post("/api/v1/runs", json={**RUN, "epochs": 1}).status_code == 422
    assert api.post("/api/v1/runs", json={**RUN, "steps": None}).status_code == 422
    r = api.post("/api/v1/runs", json={**RUN, "decisions": {"generations": 4}})
    assert r.status_code == 422 and "generations" in r.json()["detail"]
    r = api.post("/api/v1/runs", json={**RUN, "across_slots": True})
    assert r.status_code == 422 and "across_slots" in r.json()["detail"]


def test_a_run_records_the_version_of_the_dataset_it_trains_on(api):
    latest = api.post("/api/v1/runs", json={**RUN, "name": "latest"}).json()
    assert latest["dataset_version"] == 2 and latest["dataset"]["version"] == 2
    assert api.tags == {"datasets.id": "d1", "datasets.version": 2}
    chosen = api.post("/api/v1/runs", json={**RUN, "name": "chosen", "dataset_version": 1}).json()
    assert chosen["dataset_version"] == 1 and chosen["dataset"]["version"] == 1
    assert api.get(f"/api/v1/runs/{chosen['id']}").json()["dataset_version"] == 1
    r = api.post("/api/v1/runs", json={**RUN, "name": "missing", "dataset_version": 7})
    assert r.status_code == 404 and "no version 7" in r.json()["detail"]


def test_the_versions_a_run_can_train_on(api):
    assert [v["version"] for v in api.get("/api/v1/datasets/d1/versions").json()] == [2, 1]


def test_an_unknown_dataset_is_named(api):
    r = api.post("/api/v1/runs", json={**RUN, "dataset_id": "nope"})
    assert r.status_code == 404 and "nope" in r.json()["detail"]


def test_a_second_run_with_the_same_model_name_is_refused(api):
    assert api.post("/api/v1/runs", json=RUN).status_code == 200
    r = api.post("/api/v1/runs", json=RUN)
    assert r.status_code == 409 and "forge/greeter" in r.json()["detail"]


def test_a_model_that_is_not_trainable_is_refused(api):
    r = api.post("/api/v1/runs", json={**RUN, "base_model": "nvidia/Qwen3.8-27B-NVFP4"})
    assert r.status_code == 422


def test_the_name_of_a_run_that_failed_can_be_used_again(api):
    first = api.post("/api/v1/runs", json=RUN).json()
    put(api, first["id"], status="failed", error="TypeError")
    assert api.post("/api/v1/runs", json=RUN).status_code == 200


def test_a_planned_run_is_queued_at_the_end_of_its_node(api):
    a = api.post("/api/v1/runs", json={**RUN, "name": "a"}).json()
    b = api.post("/api/v1/runs", json={**RUN, "name": "b"}).json()
    for run in (a, b):
        put(api, run["id"], status="planned", plan={"decisions": [], "estimated_seconds": 600,
                                                    "memory_request_bytes": 2**34})
    assert api.post(f"/api/v1/runs/{a['id']}/queue", json={}).json()["position"] == 1
    queued = api.post(f"/api/v1/runs/{b['id']}/queue", json={"when": "slots", "across_slots": True}).json()
    assert (queued["status"], queued["position"], queued["when"], queued["across_slots"]) == ("queued", 2, "slots", True)
    assert queued["eta_seconds"] == 600
    r = api.post(f"/api/v1/runs/{a['id']}/queue", json={"when": "now", "across_slots": True})
    assert r.status_code == 422


def test_a_planning_run_asked_to_queue_queues_when_its_plan_comes(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    assert api.post(f"/api/v1/runs/{run['id']}/queue", json={}).json()["queue_on_plan"] is True


def test_the_queue_is_reordered_with_every_waiting_run_of_the_node(api):
    ids = []
    for name in ("a", "b", "c"):
        run = api.post("/api/v1/runs", json={**RUN, "name": name}).json()
        put(api, run["id"], status="planned", plan={"decisions": [], "memory_request_bytes": 2**34})
        api.post(f"/api/v1/runs/{run['id']}/queue", json={})
        ids.append(run["id"])
    r = api.put("/api/v1/queue/tkspark/order", json={"runs": [ids[2], ids[0], ids[1]]})
    assert r.status_code == 200, r.text
    assert [x["position"] for x in r.json()] == [1, 2, 3] and r.json()[0]["id"] == ids[2]
    assert api.put("/api/v1/queue/tkspark/order", json={"runs": ids[:2]}).status_code == 422
    nodes = api.get("/api/v1/queue").json()["nodes"]
    assert [r["name"] for r in nodes[0]["runs"]] == ["c", "a", "b"]
    assert api.get("/api/v1/queue").json()["worker"]["connected"] is True


def test_pause_holds_a_queued_run_and_stops_a_running_one(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="queued", position=1, plan={"decisions": []})
    paused = api.post(f"/api/v1/runs/{run['id']}/pause").json()
    assert (paused["status"], paused["paused_by"]) == ("paused", "user")
    resumed = api.post(f"/api/v1/runs/{run['id']}/resume").json()
    assert (resumed["status"], resumed["paused_by"]) == ("queued", None)
    put(api, run["id"], status="running", job_id="j1")
    stopping = api.post(f"/api/v1/runs/{run['id']}/pause").json()
    assert (stopping["status"], stopping["paused_by"]) == ("stopping", "user")
    assert api.files.stops == [run["id"]]
    put(api, run["id"], status="completed")
    assert api.post(f"/api/v1/runs/{run['id']}/pause").status_code == 409


def test_a_failed_run_with_a_plan_goes_back_to_the_queue(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="failed", error="node lost", plan={"decisions": []}, job_id="j1")
    resumed = api.post(f"/api/v1/runs/{run['id']}/resume").json()
    assert (resumed["status"], resumed["error"], resumed["position"]) == ("queued", None, 1)


def test_cancel_ends_the_job_that_holds_the_node(api):
    queued = api.post("/api/v1/runs", json={**RUN, "name": "q"}).json()
    put(api, queued["id"], status="queued", position=1)
    assert api.post(f"/api/v1/runs/{queued['id']}/cancel").json()["status"] == "cancelled"
    assert api.fake.cancelled == []
    running = api.post("/api/v1/runs", json={**RUN, "name": "r"}).json()
    put(api, running["id"], status="running", job_id="j7")
    assert api.post(f"/api/v1/runs/{running['id']}/cancel").json()["status"] == "cancelled"
    planning = api.post("/api/v1/runs", json={**RUN, "name": "p"}).json()
    put(api, planning["id"], status="planning", plan_job_id="j8")
    api.post(f"/api/v1/runs/{planning['id']}/cancel")
    preprocessing = api.post("/api/v1/runs", json={**RUN, "name": "pp"}).json()
    put(api, preprocessing["id"], preprocess={"node": "tkamd2", "job_id": "j9"})
    assert api.post(f"/api/v1/runs/{preprocessing['id']}/cancel").json()["status"] == "cancelled"
    assert api.fake.cancelled == ["j7", "j8", "j9"]


def test_a_run_that_has_not_trained_is_planned_again_with_a_persons_decisions(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="planned", plan={"decisions": []}, plan_job_id="j1")
    again = api.post(f"/api/v1/runs/{run['id']}/plan", json={"decisions": {"learning_rate": 5e-5}}).json()
    assert (again["status"], again["plan"], again["plan_job_id"], again["preprocess"]) == \
        ("preprocessing", None, None, None)
    assert again["job_spec"]["recipe"] == {"learning_rate": 5e-5} and again["job_spec"]["steps"] == 100
    put(api, run["id"], status="running", job_id="j2")
    assert api.post(f"/api/v1/runs/{run['id']}/plan", json={}).status_code == 409


def test_the_preview_formats_the_first_example_with_the_models_chat_template(api, monkeypatch):
    files = {
        "tokenizer_config.json": '{"eos_token": {"content": "</s>"}}',
        "chat_template.jinja": "{% for m in messages %}<|{{ m.role }}|>{{ m.content }}{{ eos_token }}{% endfor %}",
    }

    async def model_file(model, name):
        return files.get(name)

    monkeypatch.setattr(mlflow, "model_file", model_file)
    r = api.get("/api/v1/datasets/d1/preview", params={"model": "BSC-LT/salamandraTA-2b-instruct", "version": 1})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "<|user|>Hi?</s><|assistant|>Hello</s>", "templated": True}
    assert api.data.asked == [("d1", 1, "train")]


def test_a_model_without_a_chat_template_is_named(api, monkeypatch):
    async def model_file(model, name):
        return "{}" if name == "tokenizer_config.json" else None

    monkeypatch.setattr(mlflow, "model_file", model_file)
    r = api.get("/api/v1/datasets/d1/preview", params={"model": "BSC-LT/salamandraTA-2b-instruct", "version": 2})
    assert r.status_code == 422 and "no chat template" in r.json()["detail"]


def test_a_chat_goes_to_the_fine_tuned_model_or_to_its_base(api, monkeypatch):
    from app.services import gateway

    asked = []

    async def chat(token, model, messages, max_tokens, temperature):
        asked.append((model, messages[-1]["content"]))
        return {"content": f"answer of {model}", "usage": None}

    monkeypatch.setattr(gateway, "chat", chat)
    run = api.post("/api/v1/runs", json=RUN).json()
    body = {"messages": [{"role": "user", "content": "Bon dia"}]}
    assert api.post(f"/api/v1/runs/{run['id']}/chat", json={**body, "target": "tuned"}).json()["content"] == "answer of forge/greeter"
    assert api.post(f"/api/v1/runs/{run['id']}/chat", json={**body, "target": "base"}).json()["content"] == \
        "answer of BSC-LT/salamandraTA-2b-instruct"
    assert asked == [("forge/greeter", "Bon dia"), ("BSC-LT/salamandraTA-2b-instruct", "Bon dia")]


def test_only_a_completed_run_with_a_registered_model_is_exported(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    assert api.post(f"/api/v1/runs/{run['id']}/export", json={"node": "tkamd2"}).status_code == 409
    put(api, run["id"], status="completed")
    api.fake.job_state.update(job_id="j2", status="starting")
    r = api.post(f"/api/v1/runs/{run['id']}/export", json={"node": "tkamd2"})
    assert r.status_code == 200, r.text
    assert r.json()["export"]["model_id"] == "forge/greeter-gguf" and r.json()["export"]["job_id"] == "j2"
    assert api.fake.requests[-1]["gpus"] == 0
    assert api.post(f"/api/v1/runs/{run['id']}/export", json={"node": "tkamd2"}).status_code == 409
    api.fake.job_state.update(status="completed")
    assert api.get(f"/api/v1/runs/{run['id']}").json()["export"]["status"] == "completed"


def test_a_slot_is_checked_and_shows_its_next_window_and_its_overlaps(api):
    r = api.post("/api/v1/slots", json={"node": "tkspark", "days": [4, 0], "start": "23:00", "end": "07:00"})
    assert r.status_code == 200, r.text
    spark = r.json()
    assert (spark["days"], spark["timezone"]) == ([0, 4], "Europe/Madrid") and spark["next_window"] is not None
    gpu = api.post("/api/v1/slots", json={"node": "tkgpu", "days": [0], "start": "22:00", "end": "02:00"}).json()
    assert [o["node"] for o in gpu["overlaps"]] == ["tkspark"]
    bad = api.post("/api/v1/slots", json={"node": "tkspark", "days": [8], "start": "23:00", "end": "07:00"})
    assert bad.status_code == 422 and "Monday" in bad.json()["detail"]
    moved = api.put(f"/api/v1/slots/{gpu['id']}", json={"node": "tkgpu", "days": [2], "start": "09:00", "end": "17:00",
                                                         "timezone": "UTC"}).json()
    assert (moved["days"], moved["timezone"], moved["overlaps"]) == ([2], "UTC", [])
    assert [s["node"] for s in api.get("/api/v1/slots").json()] == ["tkgpu", "tkspark"]
    assert api.delete(f"/api/v1/slots/{gpu['id']}").status_code == 200
    assert api.delete(f"/api/v1/slots/{gpu['id']}").status_code == 404
    nodes = api.get("/api/v1/queue").json()["nodes"]
    assert nodes[0]["node"] == "tkspark" and nodes[0]["next_slot"] is not None


def test_the_worker_gets_a_control_token_of_the_person_who_connects_it(api):
    assert api.get("/api/v1/worker").json()["connected"] is True
    assert api.post("/api/v1/worker/connect").status_code == 409
    api.fake.tokens.append("Forge training queue")
    assert api.delete("/api/v1/worker/connect").json()["connected"] is False
    assert api.fake.tokens == []
    connected = api.post("/api/v1/worker/connect").json()
    assert (connected["connected"], connected["username"]) == (True, "testuser")
    assert api.fake.tokens == ["Forge training queue"]
    assert api.db.get(WorkerCredential, worker.CREDENTIAL_ID).token == "tk_worker"


def test_a_run_gets_the_priority_given_or_that_of_forge_runs(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    assert run["priority"] == 3
    urgent = api.post("/api/v1/runs", json={**RUN, "name": "urgent", "priority": 1}).json()
    assert urgent["priority"] == 1
    assert api.post("/api/v1/runs", json={**RUN, "name": "x", "priority": 6}).status_code == 422
    put_ = api.put("/api/v1/priorities", json={"forge_run": 2, "app": 4})
    assert put_.status_code == 200, put_.text
    assert {p["kind"]: p["priority"] for p in put_.json()}["app"] == 4
    assert api.post("/api/v1/runs", json={**RUN, "name": "later"}).json()["priority"] == 2
    assert api.put("/api/v1/priorities", json={"app": 0}).status_code == 422
    defaults = {p["kind"]: p["default"] for p in api.get("/api/v1/priorities").json()}
    assert defaults == {"notebook_server": 1, "notebook_job": 2, "forge_run": 3, "served_model": 4, "app": 5}


def test_a_waiting_runs_priority_is_changed_and_a_running_ones_is_not(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="queued", position=1)
    assert api.post(f"/api/v1/runs/{run['id']}/priority", json={"priority": 1}).json()["priority"] == 1
    put(api, run["id"], status="paused", paused_by="preempted", preempted_by="urgent")
    assert api.post(f"/api/v1/runs/{run['id']}/priority", json={"priority": 2}).json()["priority"] == 2
    resumed = api.post(f"/api/v1/runs/{run['id']}/resume").json()
    assert (resumed["status"], resumed["preempted_by"]) == ("queued", None)
    put(api, run["id"], status="running", job_id="j1")
    assert api.post(f"/api/v1/runs/{run['id']}/priority", json={"priority": 5}).status_code == 409


def test_a_run_evaluates_on_a_split_of_its_dataset_or_a_held_out_share(api):
    by_split = api.post("/api/v1/runs", json={**RUN, "eval_split": "test"}).json()
    assert by_split["evaluation"] == {"split": "test"}
    assert by_split["dataset"]["eval"] == {"split": "test", "path": "artifacts/3/r1/artifacts/data/test.parquet", "rows": 1}
    held = api.post("/api/v1/runs", json={**RUN, "name": "held", "eval_holdout": 0.1}).json()
    assert held["job_spec"]["eval_share"] == 0.1 and held["dataset"]["eval"] is None
    both = api.post("/api/v1/runs", json={**RUN, "name": "both", "eval_split": "test", "eval_holdout": 0.1})
    assert both.status_code == 422 and "not on both" in both.json()["detail"]
    same = api.post("/api/v1/runs", json={**RUN, "name": "same", "eval_split": "train"})
    assert same.status_code == 422 and "must not be the split" in same.json()["detail"]
    put(api, held["id"], status="planned", plan={"decisions": []})
    again = api.post(f"/api/v1/runs/{held['id']}/plan", json={"decisions": {"eval_every": 25}}).json()
    assert (again["job_spec"]["eval_share"], again["job_spec"]["eval_every"]) == (0.1, 25)


def test_lora_is_the_default_fp8_is_offered_and_the_memory_comes_from_the_plan(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    assert (run["method"], run["memory_gb"], run["cpu_cores"], run["training_memory_gb"]) == ("lora", None, None, None)
    assert api.post("/api/v1/runs", json={**RUN, "name": "f", "method": "fp8"}).json()["job_spec"]["method"] == "fp8"
    put(api, run["id"], status="planned", plan={"decisions": [
        {"name": "memory", "text": "50 GiB", "reason": "the peak, 45 GiB, and 10%"}], "memory_request_bytes": 50 * 2**30})
    shown = api.get(f"/api/v1/runs/{run['id']}").json()
    assert (shown["training_memory_gb"], shown["memory_reason"]) == (50, "50 GiB; the peak, 45 GiB, and 10%")


def test_a_run_whose_plan_cannot_size_it_is_queued_only_with_a_persons_memory(api):
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="planned", plan={"memory_request_bytes": None, "decisions": [
        {"name": "memory", "text": "unknown", "reason": "no GPU to measure on"}]})
    refused = api.post(f"/api/v1/runs/{run['id']}/queue", json={})
    assert refused.status_code == 409 and "no GPU to measure on" in refused.json()["detail"]
    assert api.post(f"/api/v1/runs/{run['id']}/resources", json={"memory_gb": 64}).json()["training_memory_gb"] == 64
    assert api.post(f"/api/v1/runs/{run['id']}/queue", json={}).json()["status"] == "queued"


GPUS = {"nodes": [{"name": "tkspark", "compute_capability": "12.1", "gpu_product": "NVIDIA-GB10", "allocations": []},
                  {"name": "tkgpu", "compute_capability": "8.6", "gpu_product": "NVIDIA-GeForce-RTX-3090",
                   "allocations": []}]}


def test_an_nvfp4_export_runs_on_a_blackwell_node_once_its_environment_exists(api, monkeypatch):
    async def gpu_status(token):
        return GPUS

    monkeypatch.setattr(control, "gpu_status", gpu_status)
    run = api.post("/api/v1/runs", json=RUN).json()
    nvfp4 = f"/api/v1/runs/{run['id']}/export/nvfp4"
    assert api.post(nvfp4, json={"node": "tkspark"}).status_code == 409
    put(api, run["id"], status="completed")
    refused = api.post(nvfp4, json={"node": "tkgpu"})
    assert refused.status_code == 422 and "8.6" in refused.json()["detail"] and "10.0 or higher" in refused.json()["detail"]
    missing = api.post(nvfp4, json={"node": "tkspark"})
    assert missing.status_code == 409
    assert "nvfp4, which does not exist" in missing.json()["detail"]
    assert "nvidia-modelopt[hf]==0.47.0" in missing.json()["detail"]
    api.fake.venvs.append({"name": "nvfp4", "status": "success"})
    api.fake.job_state.update(job_id="j5", status="starting")
    started = api.post(nvfp4, json={"node": "tkspark", "calibration_rows": 256})
    assert started.status_code == 200, started.text
    assert started.json()["nvfp4"] == {"model_id": "forge/greeter-nvfp4", "calibration_rows": 256, "node": "tkspark",
                                       "job_id": "j5", "status": "starting", "error": None}
    sent = api.fake.requests[-1]
    assert (sent["kernel_name"], sent["gpus"], sent["parameters"]["calibration_rows"]) == ("nvfp4", 1, 256)
    assert api.post(nvfp4, json={"node": "tkspark"}).status_code == 409


def test_a_failed_nvfp4_export_shows_what_its_cell_raised_such_as_controls_refusal(api, monkeypatch):
    async def gpu_status(token):
        return GPUS

    monkeypatch.setattr(control, "gpu_status", gpu_status)
    api.fake.venvs.append({"name": "nvfp4", "status": "success"})
    run = api.post("/api/v1/runs", json=RUN).json()
    put(api, run["id"], status="completed")
    api.fake.job_state.update(job_id="j6", status="starting")
    api.post(f"/api/v1/runs/{run['id']}/export/nvfp4", json={"node": "tkspark"})
    refusal = ("Thinkube Control did not publish forge/greeter-nvfp4: 422 "
               "{\"detail\":\"catalog_entry has quantization NVFP4; vllm serves a fine-tune as ['BF16']\"}")
    api.fake.job_state.update(status="error", error="cell 6 failed", results=[
        {"cell_index": 6, "success": False, "outputs": [{"output_type": "error", "ename": "RuntimeError", "evalue": refusal}]}])
    shown = api.get(f"/api/v1/runs/{run['id']}").json()["nvfp4"]
    assert (shown["status"], shown["error"]) == ("error", f"RuntimeError: {refusal}")


def test_a_completed_nvfp4_export_is_offered_in_chat(api, monkeypatch):
    from app.services import gateway

    async def chat(token, model, messages, max_tokens, temperature):
        return {"content": f"answer of {model}", "usage": None}

    monkeypatch.setattr(gateway, "chat", chat)
    run = api.post("/api/v1/runs", json=RUN).json()
    body = {"messages": [{"role": "user", "content": "Hola"}], "target": "nvfp4"}
    assert api.post(f"/api/v1/runs/{run['id']}/chat", json=body).status_code == 409
    put(api, run["id"], status="completed", nvfp4={"model_id": "forge/greeter-nvfp4", "status": "completed",
                                                  "job_id": "j7", "node": "tkspark", "calibration_rows": 512,
                                                  "error": None})
    assert api.post(f"/api/v1/runs/{run['id']}/chat", json=body).json()["content"] == "answer of forge/greeter-nvfp4"


MILESTONES = {"every": 0.1, "eval_dataset_id": "d1", "eval_dataset_version": 1, "eval_split": "test",
              "group_column": "direction", "metrics": ["chrf", "bleu"]}


def test_a_run_with_milestones_keeps_its_evaluation_set_and_meepsquared_gets_them(api):
    made = api.post("/api/v1/runs", json={**RUN, "milestones": MILESTONES})
    assert made.status_code == 200, made.text
    run = made.json()
    assert run["job_spec"]["milestones"] == 0.1
    assert run["milestones"]["metrics"] == ["chrf", "bleu"]
    assert run["milestones"]["eval"]["version"] == 1 and run["milestones"]["eval"]["split"] == "test"
    assert run["milestones"]["eval"]["group_column"] == "direction"


def test_milestones_settings_without_an_evaluation_set_or_on_a_missing_column_are_refused(api):
    without = api.post("/api/v1/runs", json={**RUN, "milestones": {"every": 0.1, "metrics": ["chrf"]}})
    assert without.status_code == 422 and "give eval_dataset_id" in without.json()["detail"]
    missing = api.post("/api/v1/runs", json={**RUN, "milestones": {**MILESTONES, "source_column": "src"}})
    assert missing.status_code == 422 and "has no column src" in missing.json()["detail"]


def test_planning_again_changes_or_removes_the_milestones_and_keeps_them_otherwise(api):
    run = api.post("/api/v1/runs", json={**RUN, "milestones": MILESTONES}).json()
    put(api, run["id"], status="planned", plan={"decisions": []})
    kept = api.post(f"/api/v1/runs/{run['id']}/plan", json={"decisions": {}}).json()
    assert kept["milestones"]["every"] == 0.1 and kept["job_spec"]["milestones"] == 0.1
    put(api, run["id"], status="planned")
    changed = api.post(f"/api/v1/runs/{run['id']}/plan", json={"milestones": {**MILESTONES, "every": 0.5}}).json()
    assert changed["milestones"]["every"] == 0.5 and changed["job_spec"]["milestones"] == 0.5
    put(api, run["id"], status="planned")
    removed = api.post(f"/api/v1/runs/{run['id']}/plan", json={"milestones": None}).json()
    assert removed["milestones"] is None and "milestones" not in removed["job_spec"]

