# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The Datasets API with MLflow and the Hugging Face Hub replaced: a file or a
Hub dataset becomes Parquet splits, its columns are mapped, and a training
gets the files and the mapping."""

import asyncio
import shutil
import time

import duckdb

import pytest

from app.api import datasets as api_module
from app.core.security import get_current_user
from app.jobs import hub_import
from app.services import huggingface, storage, tables, workflows


class Storage:
    def __init__(self, root):
        self.root = root
        self.deleted = []

    async def create(self, name, dataset_id, version):
        return "7", f"run-{dataset_id[:8]}-v{version}"

    async def upload(self, experiment_id, run_id, split, local):
        target = self.root / run_id / f"{split}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local, target)

    async def download(self, experiment_id, run_id, split, local):
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.root / run_id / f"{split}.parquet", local)

    async def delete(self, experiment_id, run_id):
        self.deleted.append(run_id)


class Argo:
    """Argo Workflows: a submitted workflow runs when the test says, as the step would."""

    def __init__(self):
        self.submitted = {}
        self.states = {}
        self.deleted = []

    def submit_import(self, dataset_id, version, experiment_id, run_id, first_row, by_split):
        name = f"datasets-import-{len(self.submitted) + 1}"
        self.submitted[name] = (experiment_id, run_id, first_row, by_split)
        self.states[name] = workflows.WorkflowState("Running", None, None)
        return name

    def state(self, name):
        return self.states.get(name)

    def delete(self, name):
        self.deleted.append(name)
        self.states.pop(name, None)

    def logs_url(self, name):
        return f"https://argo/{name}"

    def run_step(self, name, work):
        experiment_id, run_id, first_row, by_split = self.submitted[name]
        made = asyncio.run(hub_import.run(experiment_id, run_id, first_row, by_split, work))
        self.states[name] = workflows.WorkflowState("Succeeded", None, made)


class SharedSession:
    """The test's session for the background import, which closes what it opens."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.fixture
def api(client, test_db, mock_user, monkeypatch, tmp_path):
    fake = Storage(tmp_path / "mlflow")
    monkeypatch.setattr(api_module, "get_session_local", lambda: (lambda: SharedSession(test_db)))
    for name in ("create", "upload", "download", "delete"):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    monkeypatch.setattr(api_module, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(api_module, "FOLLOW_SECONDS", 0.01)
    argo = Argo()
    for name in ("submit_import", "state", "delete", "logs_url"):
        monkeypatch.setattr(workflows, name, getattr(argo, name))
    client.argo = argo

    async def user():
        return mock_user

    client.app.dependency_overrides[get_current_user] = user
    client.fake = fake
    client.tmp = tmp_path
    return client


def upload(api, name="qa.csv", content=b"question,answer\nHi?,Hello\nBye?,Bye\n"):
    r = api.post("/api/v1/datasets/upload", files={"file": (name, content)})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_upload_is_one_train_split_with_a_guessed_mapping(api):
    dataset = upload(api)
    assert dataset["status"] == "ready"
    assert dataset["splits"][0]["name"] == "train" and dataset["splits"][0]["rows"] == 2
    assert dataset["mapping"] == {"kind": "prompt", "columns": {"prompt": "question", "completion": "answer"}}
    assert [d["id"] for d in api.get("/api/v1/datasets").json()] == [dataset["id"]]


def stored(path, column):
    with duckdb.connect() as db:
        return [r[0] for r in db.sql(f"SELECT {column} FROM read_parquet('{path}')").fetchall()]


def test_an_upload_is_version_1_made_by_an_upload(api):
    dataset = upload(api)
    assert dataset["version"] == 1 and dataset["versions"] == 1 and dataset["latest"] == 1
    versions = api.get(f"/api/v1/datasets/{dataset['id']}/versions").json()
    assert [(v["version"], v["made_by"], v["made_from"], v["recipe"], v["rows"]) for v in versions] == [
        (1, "upload", None, None, 2)]
    assert api.get(f"/api/v1/datasets/{dataset['id']}/versions/2").status_code == 404
    assert api.get(f"/api/v1/datasets/{dataset['id']}/training", params={"version": 2}).status_code == 404


def test_each_row_carries_its_identity_and_a_real_origin(api):
    dataset = upload(api)
    file = api.fake.root / dataset["mlflow_run_id"] / "train.parquet"
    assert stored(file, "_row") == [0, 1]
    assert stored(file, "_origin") == [{"kind": "real", "method": None, "model": None, "model_version": None,
                                         "source_version": None, "source_row": None}] * 2
    # Not data: neither among the columns nor in what the trainer gets
    assert [c["name"] for c in dataset["columns"]] == ["question", "answer"]
    shown = api.get(f"/api/v1/datasets/{dataset['id']}/rows").json()
    assert shown["rows"][0]["_row"] == 0 and shown["version"] == 1
    assert shown["examples"][0]["example"] == {"messages": [{"role": "user", "content": "Hi?"},
                                                            {"role": "assistant", "content": "Hello"}]}
    r = api.put(f"/api/v1/datasets/{dataset['id']}/mapping", json={"kind": "text", "columns": {"text": "_row"}})
    assert r.status_code == 422


def test_a_file_with_a_reserved_column_is_refused(api):
    r = api.post("/api/v1/datasets/upload", files={"file": ("t.csv", b"_row,text\n1,a\n")})
    assert r.status_code == 422
    assert "reserved" in r.json()["detail"]


def test_rows_come_with_the_examples_the_trainer_gets(api):
    dataset = upload(api)
    shutil.rmtree(api.tmp / "cache")
    shown = api.get(f"/api/v1/datasets/{dataset['id']}/rows").json()
    assert {k: v for k, v in shown["rows"][0].items() if k in ("question", "answer", "_row")} == {
        "question": "Hi?", "answer": "Hello", "_row": 0}
    assert shown["examples"][0]["example"]["messages"][1] == {"role": "assistant", "content": "Hello"}


def test_a_mapping_that_does_not_fit_the_rows_is_refused(api):
    dataset = upload(api, "t.csv", b"a,b\nx,\n")
    r = api.put(f"/api/v1/datasets/{dataset['id']}/mapping", json={"kind": "prompt", "columns": {"prompt": "a", "completion": "b"}})
    assert r.status_code == 422
    assert "do not fit" in r.json()["detail"]
    r = api.put(f"/api/v1/datasets/{dataset['id']}/mapping", json={"kind": "text", "columns": {"text": "a"}})
    assert r.status_code == 200, r.text
    assert r.json()["mapping"] == {"kind": "text", "columns": {"text": "a"}}


def test_a_training_gets_the_files_below_the_mlflow_mount(api):
    dataset = upload(api)
    found = api.get(f"/api/v1/datasets/{dataset['id']}/training").json()
    assert found["format"] == "messages"
    assert found["version"] == 1
    assert found["splits"][0]["path"] == f"artifacts/7/run-{dataset['id'][:8]}-v1/artifacts/data/train.parquet"


def test_a_dataset_without_a_mapping_cannot_be_trained_on(api):
    dataset = upload(api, "t.csv", b"a,b\n1,2\n")
    assert dataset["mapping"] is None
    assert api.get(f"/api/v1/datasets/{dataset['id']}/training").status_code == 409


def test_an_unknown_file_type_is_refused(api):
    r = api.post("/api/v1/datasets/upload", files={"file": ("data.xlsx", b"x")})
    assert r.status_code == 422


def test_a_deleted_dataset_leaves_mlflow(api):
    dataset = upload(api)
    assert api.delete(f"/api/v1/datasets/{dataset['id']}").status_code == 200
    assert api.fake.deleted == [f"run-{dataset['id'][:8]}-v1"]
    assert api.get(f"/api/v1/datasets/{dataset['id']}").status_code == 404


def hub(api, monkeypatch, tmp_path):
    shards = {}
    for split, rows in (("train", "question,answer\nHi?,Hello\nBye?,Bye\n"), ("test", "question,answer\nYes?,No\n")):
        csv = tmp_path / f"{split}.csv"
        csv.write_text(rows)
        shards[split] = tmp_path / f"{split}-shard.parquet"
        tables.to_parquet(csv, csv.name, shards[split])

    async def files(repo):
        return [{"config": "default", "split": s, "url": str(p), "size": 10} for s, p in shards.items()]

    async def download(url, target):
        shutil.copy(url, target)

    monkeypatch.setattr(huggingface, "files", files)
    monkeypatch.setattr(huggingface, "download", download)


def settled(api, dataset_id):
    for _ in range(100):
        found = api.get(f"/api/v1/datasets/{dataset_id}").json()
        if found["status"] != "importing":
            return found
        time.sleep(0.05)
    return found


def test_a_hub_dataset_is_imported_by_a_workflow_split_by_split(api, monkeypatch, tmp_path):
    hub(api, monkeypatch, tmp_path)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    assert started["status"] == "importing" and started["origin"] == "someone/qa:default"
    assert started["workflow"] == "datasets-import-1"
    experiment_id, run_id, first_row, by_split = api.argo.submitted["datasets-import-1"]
    assert (run_id, first_row, list(by_split)) == (f"run-{started['id'][:8]}-v1", 0, ["train", "test"])

    work = tmp_path / "step"
    work.mkdir()
    api.argo.run_step("datasets-import-1", work)
    found = settled(api, started["id"])
    assert found["status"] == "ready", found
    assert {s["name"]: s["rows"] for s in found["splits"]} == {"train": 2, "test": 1}
    assert found["mapping"] == {"kind": "prompt", "columns": {"prompt": "question", "completion": "answer"}}
    # Rows are numbered across the splits, in the order the import gave them
    run = api.fake.root / run_id
    assert stored(run / "train.parquet", "_row") == [0, 1]
    assert stored(run / "test.parquet", "_row") == [2]
    version = api.get(f"/api/v1/datasets/{started['id']}/versions/1").json()
    assert version["made_by"] == "import" and version["made_from"] is None and version["rows"] == 3


def test_a_failed_workflow_fails_the_import_with_the_steps_message(api, monkeypatch, tmp_path):
    hub(api, monkeypatch, tmp_path)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    api.argo.states["datasets-import-1"] = workflows.WorkflowState("Failed", "Hugging Face answered 404", None)
    found = settled(api, started["id"])
    assert found["status"] == "failed"
    assert found["error"] == "Hugging Face answered 404 (workflow datasets-import-1: https://argo/datasets-import-1)"


def test_an_import_whose_workflow_is_gone_fails_naming_it(api, monkeypatch, tmp_path):
    hub(api, monkeypatch, tmp_path)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    del api.argo.states["datasets-import-1"]
    found = settled(api, started["id"])
    assert found["status"] == "failed" and "datasets-import-1 no longer exists" in found["error"]


def test_a_restarted_backend_follows_the_imports_again(api, monkeypatch, tmp_path, test_db):
    hub(api, monkeypatch, tmp_path)
    # The process that submitted the workflow stops before it follows it; the workflow runs on
    follow = api_module.follow
    monkeypatch.setattr(api_module, "follow", lambda version_id: None)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    monkeypatch.setattr(api_module, "follow", follow)
    work = tmp_path / "step"
    work.mkdir()
    api.argo.run_step("datasets-import-1", work)

    async def restart():
        assert api_module.reattach(test_db) == 1
        await asyncio.gather(*api_module._following.values())

    asyncio.run(restart())
    test_db.expire_all()
    assert api.get(f"/api/v1/datasets/{started['id']}").json()["status"] == "ready"


def test_deleting_an_imported_dataset_removes_its_workflow(api, monkeypatch, tmp_path):
    hub(api, monkeypatch, tmp_path)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    work = tmp_path / "step"
    work.mkdir()
    api.argo.run_step("datasets-import-1", work)
    assert settled(api, started["id"])["status"] == "ready"
    assert api.delete(f"/api/v1/datasets/{started['id']}").status_code == 200
    assert api.argo.deleted == ["datasets-import-1"]


def test_deleting_an_importing_dataset_cancels_its_workflow(api, monkeypatch, tmp_path):
    hub(api, monkeypatch, tmp_path)
    started = api.post("/api/v1/datasets/huggingface", json={"repo": "someone/qa"}).json()
    assert api.delete(f"/api/v1/datasets/{started['id']}").status_code == 200
    assert api.argo.deleted == ["datasets-import-1"]
    assert api.fake.deleted == [f"run-{started['id'][:8]}-v1"]
    assert api.get(f"/api/v1/datasets/{started['id']}").status_code == 404


def test_a_hub_dataset_with_several_configs_needs_one_chosen():
    listed = [{"config": "en", "split": "train"}, {"config": "es", "split": "train"}]
    with pytest.raises(huggingface.HubError, match=r"configs \['en', 'es'\]"):
        huggingface.choose(listed, None, None)
    assert list(huggingface.choose(listed, "es", None)) == ["train"]
