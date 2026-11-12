# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Give the rows of `identifying` versions their `_row` and `_origin`.

A version is `identifying` when its files were written without the two
columns: the migration to versions makes each existing ready dataset's
files its version 1 in that state. start.sh runs this after the migrations
and before the server, so no route sees such a version, and a start that
is cut short leaves the rest for the next start.

Each split file is rewritten in place, at the path a training that
already read it recorded. It is idempotent: the ids are the rows'
positions from the dataset's first row, so a rewrite gives the same ids
again, and a file that already has them is only read. A version becomes
ready, and the dataset's next free id is set, in one commit after all its
files are done. A version whose stored file is not a Parquet file has lost
its rows: it becomes failed, with the file named in its error.

    python -m app.identify
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.session import get_session_local
from app.models.dataset import DatasetVersion
from app.services import storage, tables

logger = logging.getLogger("app.identify")


async def identify_version(db: Session, version: DatasetVersion, work: Path) -> None:
    dataset = version.dataset
    if version.number != 1 or version.made_from is not None:
        raise RuntimeError(f"version {version.number} of dataset {dataset.name} ({dataset.id}) is identifying, but "
                           "only a first version whose files lack `_row` and `_origin` can be")
    first_row = 0
    splits = []
    for split in version.splits:
        local = work / version.mlflow_run_id / f"{split['name']}.parquet"
        await storage.download(version.mlflow_experiment_id, version.mlflow_run_id, split["name"], local)
        try:
            readable = tables.identified(local)
        except tables.TableError as e:
            # The stored file is not a Parquet file: its rows are not in MLflow, so the version cannot be ready
            version.status = "failed"
            version.error = (f"the file of split {split['name']} in MLflow "
                             f"({storage.artifact_root(version.mlflow_experiment_id, version.mlflow_run_id)}/"
                             f"{storage.split_file(split['name'])}, {local.stat().st_size} bytes) cannot be read: {e}. "
                             "Import the dataset again")
            db.commit()
            logger.error("Version 1 of dataset %s (%s) failed: %s", dataset.name, dataset.id, version.error)
            return
        if readable:
            done = local
        else:
            done = local.with_name(f"{split['name']}.identified.parquet")
            await asyncio.to_thread(tables.identify, local, done, first_row)
            await storage.upload(version.mlflow_experiment_id, version.mlflow_run_id, split["name"], done)
        described = await asyncio.to_thread(tables.describe, done)
        if described["rows"] != split["rows"]:
            raise RuntimeError(f"split {split['name']} of dataset {dataset.name} ({dataset.id}) has "
                               f"{described['rows']} rows; its version 1 records {split['rows']}")
        splits.append({**split, "bytes": done.stat().st_size})
        first_row += described["rows"]
    version.splits = splits
    version.status, version.error = "ready", None
    dataset.next_row = first_row
    db.commit()
    logger.info("Version 1 of dataset %s (%s): %d rows identified", dataset.name, dataset.id, first_row)


async def identify_all(db: Session) -> int:
    pending = db.query(DatasetVersion).filter(DatasetVersion.status == "identifying").all()
    with tempfile.TemporaryDirectory(prefix="identify-") as work:
        for version in pending:
            await identify_version(db, version, Path(work))
    return len(pending)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    db = get_session_local()()
    try:
        done = asyncio.run(identify_all(db))
    finally:
        db.close()
    logger.info("%d versions identified", done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
