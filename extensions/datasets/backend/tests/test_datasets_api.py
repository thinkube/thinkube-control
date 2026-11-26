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
from app.jobs import hub_import, ud_import
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
        self.jobs = {}
        self.states = {}
        self.deleted = []

    def submit_import(self, dataset_id, version, experiment_id, run_id, first_row, by_split, job="hub_import"):
        name = f"datasets-import-{len(self.submitted) + 1}"
        self.submitted[name] = (experiment_id, run_id, first_row, by_split)
        self.jobs[name] = job
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
        step = {"hub_import": hub_import, "ud_import": ud_import}[self.jobs[name]]
        made = asyncio.run(step.run(experiment_id, run_id, first_row, by_split, work))
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


# Universal Dependencies treebanks on a fake GitHub
def conllu_file(sent_ids):
    return "".join(f"# sent_id = {s}\n# text = Ja.\n1\tJa\tja\tINTJ\tITJ\t_\t0\troot\t_\t_\n\n" for s in sent_ids)


TREEBANKS = {
    "UD_German-GSD": {"de_gsd-ud-train.conllu": conllu_file(["g1", "g2"]),
                      "de_gsd-ud-dev.conllu": conllu_file(["g3"]),
                      "de_gsd-ud-test.conllu": conllu_file(["g4"]),
                      "README.md": "# Summary\n\n# Changelog\n\n=== Machine-readable metadata ===\nLicense: CC BY-SA 4.0\n",
                      "LICENSE.txt": "ignored, the README says it"},
    "UD_German-HDT": {"de_hdt-ud-train-a-1.conllu": conllu_file(["h1"]),
                      "de_hdt-ud-train-a-2.conllu": conllu_file(["h2", "h3"]),
                      "de_hdt-ud-test.conllu": conllu_file(["h4"]),
                      "README.md": "no metadata here\n",
                      "LICENSE.txt": "\nCC BY-SA 4.0 (Attribution-ShareAlike 4.0 International)\nmore text\n"},
    "UD_German-PUD": {"de_pud-ud-test.conllu": conllu_file(["p1"]), "README.md": "License: CC BY-SA 4.0\n"},
}


def github(api, monkeypatch, treebanks=TREEBANKS, tag="r2.18"):
    import httpx
    from app.services import ud

    def answer(request):
        url = str(request.url)
        if url.startswith("https://raw/"):
            name, file = url[len("https://raw/"):].split("/", 1)
            return httpx.Response(200, text=treebanks[name][file])
        name = request.url.path.split("/")[3]
        if name not in treebanks:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.params.get("ref") != tag:
            return httpx.Response(404, json={"message": f"No commit found for the ref {request.url.params.get('ref')}"})
        return httpx.Response(200, json=[{"type": "file", "name": f, "download_url": f"https://raw/{name}/{f}"}
                                         for f in treebanks[name]] + [{"type": "dir", "name": "not-to-release"}])

    monkeypatch.setattr(ud, "_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(answer)))


def test_treebanks_are_imported_as_one_dataset_of_words_split_by_split(api, monkeypatch, tmp_path):
    github(api, monkeypatch)
    started = api.post("/api/v1/datasets/ud", json={"treebanks": ["UD_German-GSD", "UD_German-HDT", "UD_German-PUD"],
                                                     "tag": "r2.18", "name": "German"})
    assert started.status_code == 200, started.text
    started = started.json()
    assert (started["source"], started["origin"]) == ("ud", "UD_German-GSD,UD_German-HDT,UD_German-PUD@r2.18")
    assert started["description"] == ("Universal Dependencies r2.18. UD_German-GSD: licence CC BY-SA 4.0; "
                                      "UD_German-HDT: licence CC BY-SA 4.0 (Attribution-ShareAlike 4.0 International); "
                                      "UD_German-PUD: licence CC BY-SA 4.0")
    assert api.argo.jobs["datasets-import-1"] == "ud_import"
    _, _, _, by_split = api.argo.submitted["datasets-import-1"]
    # A split holds the treebanks that have it, the parts of a large split in file-name order
    assert {s: [f["name"] for f in files] for s, files in by_split.items()} == {
        "train": ["de_gsd-ud-train.conllu", "de_hdt-ud-train-a-1.conllu", "de_hdt-ud-train-a-2.conllu"],
        "dev": ["de_gsd-ud-dev.conllu"],
        "test": ["de_gsd-ud-test.conllu", "de_hdt-ud-test.conllu", "de_pud-ud-test.conllu"]}

    work = tmp_path / "step"
    work.mkdir()
    api.argo.run_step("datasets-import-1", work)
    found = settled(api, started["id"])
    assert found["status"] == "ready", found
    assert {s["name"]: s["rows"] for s in found["splits"]} == {"train": 5, "dev": 1, "test": 3}
    assert found["mapping"]["kind"] == "words" and found["mapping"]["columns"]["deprel"] == "deprel"
    rows = api.get(f"/api/v1/datasets/{started['id']}/rows", params={"split": "test"}).json()
    assert [(r["treebank"], r["sent_id"]) for r in rows["rows"]] == [
        ("UD_German-GSD", "g4"), ("UD_German-HDT", "h4"), ("UD_German-PUD", "p1")]
    assert rows["examples"][0]["example"]["head"] == [0]
    training = api.get(f"/api/v1/datasets/{started['id']}/training").json()
    assert training["format"] == "words"


def test_a_tag_a_treebank_does_not_have_is_refused_naming_it(api, monkeypatch):
    github(api, monkeypatch)
    r = api.post("/api/v1/datasets/ud", json={"treebanks": ["UD_German-GSD"], "tag": "r9.99"})
    assert r.status_code == 422 and r.json()["detail"] == "UniversalDependencies/UD_German-GSD has no tag r9.99"
    assert api.argo.submitted == {}


def test_a_missing_treebank_is_refused_naming_it(api, monkeypatch):
    github(api, monkeypatch)
    r = api.post("/api/v1/datasets/ud", json={"treebanks": ["UD_German-GSD", "UD_German-XYZ"], "tag": "r2.18"})
    assert r.status_code == 422 and "has no treebank UD_German-XYZ" in r.json()["detail"]


def test_a_treebank_without_conllu_files_is_refused_naming_it(api, monkeypatch):
    github(api, monkeypatch, {"UD_German-Empty": {"README.md": "License: CC0\n"}})
    r = api.post("/api/v1/datasets/ud", json={"treebanks": ["UD_German-Empty"], "tag": "r2.18"})
    assert r.status_code == 422
    assert r.json()["detail"] == ("UniversalDependencies/UD_German-Empty at r2.18 has no -ud-train, -ud-dev or "
                                  "-ud-test .conllu file")


def test_a_treebank_that_states_no_licence_is_refused(api, monkeypatch):
    github(api, monkeypatch, {"UD_German-Quiet": {"de_q-ud-test.conllu": conllu_file(["q1"])}})
    r = api.post("/api/v1/datasets/ud", json={"treebanks": ["UD_German-Quiet"], "tag": "r2.18"})
    assert r.status_code == 422 and "states no licence" in r.json()["detail"]


def test_a_name_that_is_not_a_treebank_is_refused(api, monkeypatch):
    github(api, monkeypatch)
    r = api.post("/api/v1/datasets/ud", json={"treebanks": ["../etc"], "tag": "r2.18"})
    assert r.status_code == 422 and "not treebank names" in r.json()["detail"]


def test_an_uploaded_conllu_file_is_a_dataset_of_words(api):
    dataset = upload(api, "de_x-ud-train.conllu", conllu_file(["u1", "u2"]).encode())
    assert dataset["splits"] == [{"name": "train", "rows": 2, "bytes": dataset["splits"][0]["bytes"]}]
    assert dataset["mapping"]["kind"] == "words"
