# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Datasets kept before versions: the migration makes each one's files its
version 1, and the data step gives their rows `_row` and `_origin` in place,
once, however often it runs."""

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app import identify
from app.models.dataset import Dataset, DatasetVersion
from app.services import storage, tables

BACKEND = Path(__file__).resolve().parents[1]


class Storage:
    def __init__(self, root):
        self.root = root
        self.uploads = 0

    async def download(self, experiment_id, run_id, split, local):
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.root / run_id / f"{split}.parquet", local)

    async def upload(self, experiment_id, run_id, split, local):
        self.uploads += 1
        shutil.copy(local, self.root / run_id / f"{split}.parquet")


def plain(path, csv_rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    csv = path.with_suffix(".csv")
    csv.write_text(csv_rows)
    tables.to_parquet(csv, csv.name, path)


def rows_of(path):
    with duckdb.connect() as db:
        return db.sql(f"SELECT _row, _origin.kind, q FROM read_parquet('{path}')").fetchall()


def test_the_data_step_identifies_version_1_in_place_and_once(test_db, monkeypatch, tmp_path):
    fake = Storage(tmp_path / "mlflow")
    for name in ("download", "upload"):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    run = fake.root / "run-1"
    plain(run / "train.parquet", "q,a\nHi?,Hello\nBye?,Bye\n")
    plain(run / "test.parquet", "q,a\nYes?,No\n")
    dataset = Dataset(id=str(uuid.uuid4()), name="qa", source="upload", origin="qa.csv", next_row=0, created_by="me")
    dataset.versions.append(DatasetVersion(
        id=str(uuid.uuid4()), number=1, status="identifying", made_by="upload", created_by="me",
        splits=[{"name": "train", "rows": 2, "bytes": 1}, {"name": "test", "rows": 1, "bytes": 1}],
        columns=[{"name": "q", "type": "VARCHAR"}, {"name": "a", "type": "VARCHAR"}],
        mlflow_experiment_id="7", mlflow_run_id="run-1"))
    test_db.add(dataset)
    test_db.commit()

    assert asyncio.run(identify.identify_all(test_db)) == 1
    assert rows_of(run / "train.parquet") == [(0, "real", "Hi?"), (1, "real", "Bye?")]
    assert rows_of(run / "test.parquet") == [(2, "real", "Yes?")]
    version = dataset.versions[0]
    assert version.status == "ready" and dataset.next_row == 3
    assert version.splits[0]["bytes"] == (run / "train.parquet").stat().st_size

    # Cut short after its files were written: the next start reads them and writes nothing
    version.status = "identifying"
    test_db.commit()
    uploads = fake.uploads
    assert asyncio.run(identify.identify_all(test_db)) == 1
    assert fake.uploads == uploads
    assert rows_of(run / "train.parquet") == [(0, "real", "Hi?"), (1, "real", "Bye?")]
    assert asyncio.run(identify.identify_all(test_db)) == 0


def test_the_migration_makes_each_dataset_its_version_1(db_engine):
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        migrations = Config(str(BACKEND / "alembic.ini"))
        migrations.set_main_option("script_location", str(BACKEND / "alembic"))
        migrations.attributes["connection"] = connection
        try:
            command.upgrade(migrations, "c3a1d7e5f902")
            created = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for id_, source, status in (("a", "huggingface", "ready"), ("b", "upload", "failed")):
                connection.execute(text(
                    "INSERT INTO datasets (id, name, source, origin, status, error, splits, columns, mapping, "
                    "mlflow_experiment_id, mlflow_run_id, created_by, created_at) VALUES (:id, 'n', :source, 'o', "
                    ":status, NULL, '[{\"name\": \"train\", \"rows\": 2, \"bytes\": 9}]', '[]', "
                    "'{\"kind\": \"text\", \"columns\": {\"text\": \"t\"}}', '3', 'run', 'me', :created)"),
                    {"id": id_, "source": source, "status": status, "created": created})
            command.upgrade(migrations, "head")
            found = connection.execute(text(
                "SELECT dataset_id, number, status, made_by, made_from, recipe, mlflow_run_id, mapping::text, "
                "created_at FROM dataset_versions ORDER BY dataset_id")).fetchall()
            assert [tuple(r[:7]) for r in found] == [
                ("a", 1, "identifying", "import", None, None, "run"),
                ("b", 1, "failed", "upload", None, None, "run"),
            ]
            assert '"text"' in found[0][7] and found[0][8] == created
            assert connection.execute(text("SELECT next_row FROM datasets WHERE id = 'a'")).scalar() == 0
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_a_version_whose_stored_file_is_empty_fails_naming_it(test_db, monkeypatch, tmp_path):
    fake = Storage(tmp_path / "mlflow")
    for name in ("download", "upload"):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    (fake.root / "run-2").mkdir(parents=True)
    (fake.root / "run-2" / "train.parquet").write_bytes(b"")
    dataset = Dataset(id=str(uuid.uuid4()), name="empty", source="huggingface", origin="x/y:default", next_row=0,
                      created_by="me")
    dataset.versions.append(DatasetVersion(
        id=str(uuid.uuid4()), number=1, status="identifying", made_by="import", created_by="me",
        splits=[{"name": "train", "rows": 1000, "bytes": 962969}], columns=[{"name": "text", "type": "VARCHAR"}],
        mlflow_experiment_id="3", mlflow_run_id="run-2"))
    test_db.add(dataset)
    test_db.commit()

    assert asyncio.run(identify.identify_all(test_db)) == 1
    version = dataset.versions[0]
    assert version.status == "failed" and fake.uploads == 0
    assert "3/run-2/artifacts/data/train.parquet, 0 bytes" in version.error
    assert asyncio.run(identify.identify_all(test_db)) == 0
