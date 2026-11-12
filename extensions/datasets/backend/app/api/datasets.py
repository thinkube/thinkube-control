# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Datasets: uploaded files and Hugging Face datasets, kept as Parquet in
MLflow, in numbered versions, with the mapping of their columns to training
examples.

A route that reads a dataset acts on the version given as `version`, or on
the latest ready version when none is given."""

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import User, get_current_user
from app.db.session import get_db, get_session_local
from app.models.dataset import Dataset, DatasetVersion
from app.services import huggingface, mapping, storage, tables, workflows

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE = Path(tempfile.gettempdir()) / "datasets-cache"
MAX_UPLOAD_BYTES = 2 * 1024 ** 3
PREVIEW_LIMIT = 100
UPLOAD_SPLIT = "train"
# How often an import's workflow is read, and how many reads in a row Argo
# may leave unanswered before the import is recorded as failed
FOLLOW_SECONDS = 5
FOLLOW_TRIES = 60

# The workflows this process follows, by version id, kept so the event loop does not drop them
_following: Dict[str, asyncio.Task] = {}


def _dataset(db: Session, dataset_id: str) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found")
    return dataset


def _version(dataset: Dataset, number: Optional[int]) -> DatasetVersion:
    """The version a route acts on: the one given, or the latest ready one."""
    if number is None:
        version = dataset.described()
    else:
        version = next((v for v in dataset.versions if v.number == number), None)
        if version is None:
            raise HTTPException(status_code=404, detail=f"dataset {dataset.name} has no version {number}")
    return version


def _ready(dataset: Dataset, number: Optional[int]) -> DatasetVersion:
    version = _version(dataset, number)
    if version.status != "ready":
        state = f"{version.status}: {version.error}" if version.error else version.status
        raise HTTPException(status_code=409, detail=f"version {version.number} of dataset {dataset.name} is {state}")
    return version


def reattach(db: Session) -> int:
    """Follow again the workflows of the imports in progress: they ran on
    while this process was away."""
    found = db.query(DatasetVersion).filter(DatasetVersion.status == "importing").all()
    for version in found:
        follow(version.id)
    return len(found)


@router.get("", operation_id="datasets_list")
async def list_datasets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every dataset, described by its latest ready version."""
    return [d.to_dict() for d in db.query(Dataset).order_by(Dataset.created_at.desc()).all()]


@router.get("/upload-target", operation_id="datasets_upload_target")
async def upload_target(user: User = Depends(get_current_user)):
    """Where a file is sent to become a dataset, and how: a multipart POST with the caller's own token. A file
    does not fit in an MCP call; the POST takes it from disk, and answers with the new dataset once it is stored."""
    url = f"{settings.FRONTEND_URL}{settings.API_V1_STR}/datasets/upload"
    return {
        "method": "POST",
        "url": url,
        "headers": {"Authorization": "Bearer <the token this call was made with: a Thinkube Control API token>"},
        "form": {"file": f"the file, .jsonl, .json, .csv or .parquet, at most {MAX_UPLOAD_BYTES // 1024 ** 3} GiB; "
                         f"it becomes the split {UPLOAD_SPLIT} of version 1",
                 "name": "the dataset's name; the file's name without its extension when not given"},
        "curl": f"curl -fsS -H \"Authorization: Bearer $TOKEN\" -F file=@<path> -F name=<name> {url}",
    }


@router.get("/{dataset_id}", operation_id="datasets_get")
async def get_dataset(dataset_id: str, version: Optional[int] = None, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """The dataset, described by the version given or its latest ready one."""
    dataset = _dataset(db, dataset_id)
    return {**dataset.to_dict(), **_version(dataset, version).fields()}


@router.get("/{dataset_id}/versions", operation_id="datasets_list_versions")
async def list_versions(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The versions of a dataset, the newest first, each with its lineage and row counts."""
    return [v.to_dict() for v in reversed(_dataset(db, dataset_id).versions)]


@router.get("/{dataset_id}/versions/{number}", operation_id="datasets_get_version")
async def get_version(dataset_id: str, number: int, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """A version of a dataset: its state, lineage, splits with their row counts, columns and mapping."""
    return _version(_dataset(db, dataset_id), number).to_dict()


def _cache(run_id: str, split: str) -> Path:
    return CACHE / run_id / f"{split}.parquet"


async def _store(dataset: Dataset, version: DatasetVersion, files: Dict[str, Path]) -> None:
    """Give each split's rows their identity and origin, put the splits in
    the version's MLflow run and keep them in the local cache."""
    experiment_id, run_id = await storage.create(dataset.name, dataset.id, version.number)
    first_row = dataset.next_row
    splits, columns = [], None
    for split, path in files.items():
        identified = path.with_name(f"{split}.identified.parquet")
        first_row += await asyncio.to_thread(tables.identify, path, identified, first_row)
        described = await asyncio.to_thread(tables.describe, identified)
        columns = columns or described["columns"]
        await storage.upload(experiment_id, run_id, split, identified)
        cached = _cache(run_id, split)
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(identified), cached)
        splits.append({"name": split, "rows": described["rows"], "bytes": cached.stat().st_size})
    sample = await asyncio.to_thread(tables.head, _cache(run_id, splits[0]["name"]), 0, 1)
    version.mlflow_experiment_id, version.mlflow_run_id = experiment_id, run_id
    version.splits, version.columns = splits, columns
    version.mapping = mapping.guess([c["name"] for c in columns]) if sample else None
    version.status, version.error = "ready", None
    dataset.next_row = first_row


def _first_version(dataset: Dataset, made_by: str, status: str) -> DatasetVersion:
    version = DatasetVersion(id=str(uuid.uuid4()), number=1, status=status, splits=[], columns=[], made_from=None,
                             made_by=made_by, recipe=None, created_by=dataset.created_by)
    dataset.versions.append(version)
    return version


@router.post("/upload")
async def upload(file: UploadFile = File(...), name: Optional[str] = Form(None),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A file as a dataset of one split, train, in its version 1."""
    try:
        tables.reader(file.filename or "")
    except tables.TableError as e:
        raise HTTPException(status_code=422, detail=str(e))
    work = Path(tempfile.mkdtemp(prefix="upload-"))
    try:
        raw = work / Path(file.filename).name
        size = 0
        with raw.open("wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"a file can be at most {MAX_UPLOAD_BYTES // 1024 ** 3} GiB")
                f.write(chunk)
        parquet = work / f"{UPLOAD_SPLIT}.parquet"
        try:
            await asyncio.to_thread(tables.to_parquet, raw, file.filename, parquet)
        except tables.TableError as e:
            raise HTTPException(status_code=422, detail=str(e))
        dataset = Dataset(id=str(uuid.uuid4()), name=name or Path(file.filename).stem, source="upload",
                          origin=file.filename, next_row=0, created_by=user.preferred_username)
        version = _first_version(dataset, "upload", "importing")
        try:
            await _store(dataset, version, {UPLOAD_SPLIT: parquet})
        except tables.TableError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except storage.StorageError as e:
            raise HTTPException(status_code=502, detail=str(e))
        db.add(dataset)
        db.commit()
        db.refresh(dataset)
        return dataset.to_dict()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class HubImport(BaseModel):
    repo: str = Field(..., min_length=3, max_length=255, pattern=r"^[\w.-]+/[\w.-]+$")
    config: Optional[str] = None
    splits: Optional[List[str]] = None
    name: Optional[str] = Field(None, max_length=255)


def follow(version_id: str) -> None:
    if version_id not in _following:
        _following[version_id] = asyncio.create_task(_follow(version_id))


def _settle(dataset: Dataset, version: DatasetVersion, found: Optional[workflows.WorkflowState]) -> bool:
    """Record what the workflow did, once it has ended; False while it runs."""
    if found is None:
        version.status = "failed"
        version.error = f"the import's workflow {version.workflow} no longer exists in Argo; import the dataset again"
        return True
    if found.phase == "Succeeded":
        made = found.result
        version.splits, version.columns, version.mapping = made["splits"], made["columns"], made["mapping"]
        version.status, version.error = "ready", None
        dataset.next_row = made["next_row"]
        return True
    if found.phase in ("Failed", "Error"):
        version.status = "failed"
        version.error = f"{found.message or found.phase} (workflow {version.workflow}: {workflows.logs_url(version.workflow)})"
        return True
    return False


async def _follow(version_id: str) -> None:
    """Follow an import's workflow until it ends, and record its result."""
    unanswered = 0
    try:
        while True:
            db = get_session_local()()
            try:
                version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
                if version is None or version.status != "importing":
                    return
                try:
                    found = await asyncio.to_thread(workflows.state, version.workflow)
                    unanswered = 0
                except workflows.WorkflowsError as e:
                    unanswered += 1
                    logger.warning("Workflow %s of version %s: %s", version.workflow, version_id, e)
                    if unanswered < FOLLOW_TRIES:
                        await asyncio.sleep(FOLLOW_SECONDS)
                        continue
                    version.status = "failed"
                    version.error = f"Argo left {FOLLOW_TRIES} reads of workflow {version.workflow} unanswered: {e}"
                    db.commit()
                    return
                if _settle(version.dataset, version, found):
                    db.commit()
                    return
            finally:
                db.close()
            await asyncio.sleep(FOLLOW_SECONDS)
    except Exception:
        logger.exception("Following the import of version %s failed", version_id)
    finally:
        _following.pop(version_id, None)


@router.post("/huggingface", operation_id="datasets_import_huggingface")
async def import_from_hub(request: HubImport, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start importing a Hugging Face dataset as an Argo workflow; it is listed
    as importing until the workflow has ended."""
    try:
        by_split = huggingface.choose(await huggingface.files(request.repo), request.config, request.splits)
    except huggingface.HubError as e:
        raise HTTPException(status_code=422, detail=str(e))
    config = next(iter(by_split.values()))[0]["config"]
    dataset = Dataset(id=str(uuid.uuid4()), name=request.name or request.repo.split("/")[-1], source="huggingface",
                      origin=f"{request.repo}:{config}", next_row=0, created_by=user.preferred_username)
    version = _first_version(dataset, "import", "importing")
    try:
        version.mlflow_experiment_id, version.mlflow_run_id = await storage.create(dataset.name, dataset.id,
                                                                                   version.number)
    except storage.StorageError as e:
        raise HTTPException(status_code=502, detail=str(e))
    files = {split: [{"url": f["url"]} for f in shards] for split, shards in by_split.items()}
    try:
        version.workflow = await asyncio.to_thread(workflows.submit_import, dataset.id, version.number,
                                                   version.mlflow_experiment_id, version.mlflow_run_id,
                                                   dataset.next_row, files)
    except workflows.WorkflowsError as e:
        try:
            await storage.delete(version.mlflow_experiment_id, version.mlflow_run_id)
        except storage.StorageError as cleanup:
            raise HTTPException(status_code=502, detail=f"{e}; and the run made for it stays: {cleanup}")
        raise HTTPException(status_code=502, detail=str(e))
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    follow(version.id)
    return dataset.to_dict()


async def _cached(dataset: Dataset, version: DatasetVersion, split: str) -> Path:
    if split not in [s["name"] for s in version.splits]:
        raise HTTPException(status_code=404, detail=f"version {version.number} of dataset {dataset.name} has no split {split}")
    path = _cache(version.mlflow_run_id, split)
    if not path.exists():
        try:
            await storage.download(version.mlflow_experiment_id, version.mlflow_run_id, split, path)
        except storage.StorageError as e:
            raise HTTPException(status_code=502, detail=str(e))
    return path


@router.get("/{dataset_id}/rows", operation_id="datasets_rows")
async def rows(dataset_id: str, split: Optional[str] = None, offset: int = 0, limit: int = 20,
               version: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rows of a split of a version, and each as the trainer gets it with the version's mapping."""
    dataset = _dataset(db, dataset_id)
    chosen = _ready(dataset, version)
    split = split or chosen.splits[0]["name"]
    path = await _cached(dataset, chosen, split)
    found = await asyncio.to_thread(tables.head, path, max(offset, 0), min(max(limit, 1), PREVIEW_LIMIT))
    return {"version": chosen.number, "split": split, "offset": offset, "rows": found,
            "examples": mapping.preview(found, chosen.mapping)}


class Mapping(BaseModel):
    kind: str
    columns: Dict[str, Optional[str]]


@router.put("/{dataset_id}/mapping", operation_id="datasets_set_mapping")
async def set_mapping(dataset_id: str, new: Mapping, version: Optional[int] = None,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Map a version's columns to examples; refused when its first rows do not fit it."""
    dataset = _dataset(db, dataset_id)
    chosen = _ready(dataset, version)
    try:
        checked = mapping.check(new.model_dump(), [c["name"] for c in chosen.columns])
    except mapping.MappingError as e:
        raise HTTPException(status_code=422, detail=str(e))
    path = await _cached(dataset, chosen, chosen.splits[0]["name"])
    sample = await asyncio.to_thread(tables.head, path, 0, 5)
    failed = [p["error"] for p in mapping.preview(sample, checked) if "error" in p]
    if failed:
        raise HTTPException(status_code=422, detail=f"the first rows do not fit this mapping: {failed[0]}")
    chosen.mapping = checked
    db.commit()
    db.refresh(dataset)
    return {**dataset.to_dict(), **chosen.fields()}


@router.get("/{dataset_id}/training", operation_id="datasets_training")
async def training(dataset_id: str, version: Optional[int] = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """What a training reads of a version: each split's file below ~/thinkube/mlflow, and the mapping."""
    dataset = _dataset(db, dataset_id)
    chosen = _ready(dataset, version)
    if chosen.mapping is None:
        raise HTTPException(status_code=409, detail=f"version {chosen.number} of dataset {dataset.name} has no "
                                                    "mapping of its columns yet")
    root = storage.artifact_root(chosen.mlflow_experiment_id, chosen.mlflow_run_id)
    return {
        "id": dataset.id,
        "name": dataset.name,
        "version": chosen.number,
        "mapping": chosen.mapping,
        "format": mapping.output_format(chosen.mapping),
        "splits": [{**s, "path": f"artifacts/{root}/{storage.split_file(s['name'])}"} for s in chosen.splits],
    }


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The dataset, every version, their files and their workflows; an import in progress is cancelled."""
    dataset = _dataset(db, dataset_id)
    busy = [v.number for v in dataset.versions if v.status == "identifying"]
    if busy:
        raise HTTPException(status_code=409, detail=f"dataset {dataset.name} has versions {busy} being identified; "
                                                    "delete it when Datasets has started")
    for version in dataset.versions:
        try:
            # The workflow goes first: one in progress is cancelled, so that it writes nothing more
            if version.workflow:
                await asyncio.to_thread(workflows.delete, version.workflow)
            if version.mlflow_run_id:
                await storage.delete(version.mlflow_experiment_id, version.mlflow_run_id)
        except (workflows.WorkflowsError, storage.StorageError) as e:
            raise HTTPException(status_code=502, detail=str(e))
            shutil.rmtree(CACHE / version.mlflow_run_id, ignore_errors=True)
    db.delete(dataset)
    db.commit()
    return {"deleted": dataset_id}
