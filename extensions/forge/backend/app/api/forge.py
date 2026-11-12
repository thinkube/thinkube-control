# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Forge's API: the models that can be fine-tuned, the GPU nodes, datasets,
runs with their progress and their model, the training queue and its slots."""

import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from jinja2 import TemplateError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.security import User, get_current_user, oauth2_scheme
from app.db.session import get_db
from app.models.forge import NodeState, Run, Slot, WorkerCredential
from app.services import (chat_template, control, datasets, gateway, mlflow, priorities, runfiles, runs,
                          slots, worker)

router = APIRouter()


def _control_failed(e: control.ControlError) -> HTTPException:
    return HTTPException(status_code=e.status if 400 <= e.status < 500 else 502, detail=e.detail)


def _datasets_failed(e: datasets.DatasetsError) -> HTTPException:
    return HTTPException(status_code=e.status if 400 <= e.status < 500 else 502, detail=e.detail)


async def _catalog(token: str) -> List[Dict[str, Any]]:
    try:
        return (await control.catalog(token))["models"]
    except control.ControlError as e:
        raise _control_failed(e)


def trainable(model: Dict[str, Any]) -> bool:
    """A mirrored text model in 16-bit weights, which LoRA and QLoRA train."""
    return (model.get("is_downloaded") and model.get("task") == "text-generation"
            and model.get("quantization") == "BF16" and not model.get("is_finetuned"))


@router.get("/models", operation_id="forge_list_models")
async def list_models(user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    """The models Forge can fine-tune: the mirrored BF16 text models of the catalogue."""
    return [m for m in await _catalog(token) if trainable(m)]


@router.get("/nodes", operation_id="forge_list_nodes")
async def list_nodes(user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    """The nodes with GPUs, with their memory and the models loaded on them."""
    try:
        status = await control.gpu_status(token)
    except control.ControlError as e:
        raise _control_failed(e)
    return [
        {
            "name": n["name"],
            "gpu_count": n["gpu_count"],
            "total_memory_gb": n["total_memory_gb"],
            "ai_remaining_gb": n.get("ai_remaining_gb"),
            "loaded_models": [a["model_id"] for a in n.get("allocations", [])],
            "compute_capability": n.get("compute_capability"),
            "nvfp4": runs.nvfp4_capable(n.get("compute_capability")),
        }
        for n in status["nodes"] if n["gpu_count"] > 0
    ]


# --- Datasets ------------------------------------------------------------

@router.get("/datasets", operation_id="forge_list_datasets")
async def list_datasets(user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    """The datasets of the Datasets extension a run can train on."""
    try:
        return await datasets.trainable(token)
    except datasets.DatasetsError as e:
        raise _datasets_failed(e)


@router.get("/datasets/{dataset_id}/versions", operation_id="forge_list_dataset_versions")
async def list_dataset_versions(dataset_id: str, user: User = Depends(get_current_user),
                                token: str = Depends(oauth2_scheme)):
    """The versions of a dataset a run can train on, the newest first."""
    try:
        return await datasets.versions(token, dataset_id)
    except datasets.DatasetsError as e:
        raise _datasets_failed(e)


@router.get("/datasets/{dataset_id}/preview", operation_id="forge_preview_example")
async def preview(dataset_id: str, model: str, version: int, split: str = "train",
                  user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    """The first example of a split of a version as the base model trains on it: through its chat template, or as text."""
    try:
        example = await datasets.first_example(token, dataset_id, version, split)
    except datasets.DatasetsError as e:
        raise _datasets_failed(e)
    if example is None:
        raise HTTPException(status_code=422, detail=f"the first row of {split} does not fit the dataset's mapping")
    if "text" in example:
        return {"text": example["text"], "templated": False}
    try:
        chat = chat_template.template_of(await mlflow.model_file(model, "tokenizer_config.json"),
                                         await mlflow.model_file(model, "chat_template.jinja"))
        return {"text": chat_template.render(chat, example["messages"]), "templated": True}
    except mlflow.MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except chat_template.TemplateMissing as e:
        raise HTTPException(status_code=422, detail=str(e))
    except TemplateError as e:
        raise HTTPException(status_code=422, detail=f"the model's chat template cannot format this example: {e}")


# --- Runs: Meep² training jobs in a queue per node --------------------------

DECISIONS = (f"decisions of Meep²'s plan, by name, each the person's: of the recipe {list(runs.RECIPE_FIELDS)}, "
             f"of the overrides {list(runs.OVERRIDE_FIELDS)}, or of the job {list(runs.JOB_FIELDS)}; Meep²'s auto "
             "mode decides every one not given")

class Milestones(BaseModel):
    """Meep²'s milestones: the adapter kept apart at a share of the training and, with an evaluation set, the
    model's answers to it scored by the scoring service."""

    every: float = Field(..., gt=0, le=1, description="the share of the planned steps between milestones, such as "
                                                       "0.02 for every 2%; there is one at the last step too")
    eval_dataset_id: Optional[str] = Field(None, description="the evaluation set: a dataset of Datasets whose mapping "
                                                             "gives each row's prompt and its expected answer; "
                                                             "without it a milestone keeps the adapter only")
    eval_dataset_version: Optional[int] = Field(None, ge=1, description="its version; its latest ready one when "
                                                                        "not given")
    eval_split: str = Field("train", description="the split of the evaluation set")
    group_column: Optional[str] = Field(None, description="a column whose value groups the rows, each group scored "
                                                          "apart, such as the translation direction")
    source_column: Optional[str] = Field(None, description="a column holding each row's source alone, for the "
                                                           "answer's length cap (2 x its tokens + 32); the prompt's "
                                                           "last user message when not given")
    metrics: List[str] = Field([], description="the scoring service's metrics computed on each group's answers, "
                                               "such as ['chrf', 'bleu']; required with an evaluation set")


async def _milestones(token: str, wanted: Optional[Milestones]) -> Optional[Dict[str, Any]]:
    """The run's milestones as Forge keeps them, the evaluation set read from Datasets."""
    if wanted is None:
        return None
    training, columns = None, []
    if wanted.eval_dataset_id is not None:
        try:
            training = await datasets.training(token, wanted.eval_dataset_id, wanted.eval_dataset_version)
            columns = [c["name"] for c in
                       (await datasets.version(token, wanted.eval_dataset_id, training["version"]))["columns"]]
        except datasets.DatasetsError as e:
            raise _datasets_failed(e)
    elif wanted.group_column or wanted.source_column or wanted.metrics or wanted.eval_dataset_version:
        raise HTTPException(status_code=422, detail="milestones: group_column, source_column, metrics and "
                                                    "eval_dataset_version are settings of an evaluation set; give "
                                                    "eval_dataset_id")
    try:
        return runs.milestones_of(wanted.every, training, columns, wanted.eval_split, wanted.group_column,
                                  wanted.source_column, wanted.metrics)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class NewRun(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=120)
    dataset_id: str
    # The version of the dataset the run trains on; its latest ready version when not given
    dataset_version: Optional[int] = Field(None, ge=1)
    train_split: str = "train"
    # A split of the dataset to evaluate on, or a share of the training split held out, or neither
    eval_split: Optional[str] = None
    eval_holdout: float = Field(0, ge=0, le=runs.MAX_HOLDOUT)
    base_model: str
    method: Literal["lora", "qlora", "fp8"] = "lora"
    node: str
    register_model: bool = Field(True, alias="register")
    steps: Optional[int] = Field(None, gt=0, le=10_000_000)
    epochs: Optional[float] = Field(None, gt=0, le=100)
    # Decisions of Meep²'s plan the person sets, by name: fields of Meep²'s Recipe or Overrides
    decisions: Dict[str, Any] = Field({}, description=DECISIONS)
    milestones: Optional[Milestones] = None
    when: Literal["now", "slots"] = "now"
    across_slots: bool = False
    # 1 (highest) to 5 (lowest); the priority of Forge runs on the Priorities page when not given
    priority: Optional[int] = Field(None, ge=1, le=5)
    # Put the run in its node's queue as soon as its plan is ready
    queue: bool = False
    # Advanced: the job's memory and CPU cores, the person's choice; Forge sizes the memory and the node's
    # default gives the CPU cores when they are not set
    memory_gb: Optional[int] = Field(None, ge=4, le=1024)
    cpu_cores: Optional[int] = Field(None, ge=1, le=256)


def _queue_connected(db: Session) -> None:
    if worker.credential(db) is None:
        raise HTTPException(status_code=409, detail="the training queue has no Control token; connect it on "
                                                    "Forge's Slots page first, so that it can start jobs for you")


async def _meepsquared_environment(token: str) -> None:
    try:
        found = next((v for v in await control.venvs(token) if v["name"] == runs.MEEPSQUARED_KERNEL), None)
    except control.ControlError as e:
        raise _control_failed(e)
    if found is None or found.get("status") != "success":
        state = "is not built" if found else "does not exist"
        raise HTTPException(status_code=422, detail=f"the Python environment {runs.MEEPSQUARED_KERNEL} {state}; create it in "
                                                    "Thinkube Control (Jupyter environments) with the package meepsquared")


def _run_dict(run: Run) -> Dict[str, Any]:
    """The run, with its time left and the memory its training job asks for: the person's, or Meep²'s
    plan's; null, with Meep²'s reason, while the plan does not size it."""
    memory = run.memory_gb if run.memory_gb is not None else (runs.train_memory_gb(run) if runs.sized(run) else None)
    return {**run.to_dict(), "eta_seconds": runs.eta_seconds(run), "training_memory_gb": memory,
            "memory_reason": runs.memory_reason(run.plan) if run.plan else None}


@router.post("/runs", operation_id="forge_create_run")
async def create_run(new: NewRun, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)):
    """A new run: Forge builds its Meep² job, and the queue worker asks Meep² for its plan on the node."""
    _queue_connected(db)
    milestones = await _milestones(token, new.milestones)
    try:
        evaluation = runs.evaluation_of(new.eval_split, new.eval_holdout)
        job = runs.meepsquared_job(new.method, new.steps, new.epochs, new.decisions, evaluation, milestones)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if new.when == "now" and new.across_slots:
        raise HTTPException(status_code=422, detail="across_slots is for a run that waits for slots")
    await _meepsquared_environment(token)
    try:
        dataset = runs.dataset_for_run(await datasets.training(token, new.dataset_id, new.dataset_version),
                                       new.train_split, evaluation)
    except datasets.DatasetsError as e:
        raise _datasets_failed(e)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    base = next((m for m in await _catalog(token) if m["id"] == new.base_model), None)
    if base is None or not trainable(base):
        raise HTTPException(status_code=422, detail=f"{new.base_model} is not a mirrored BF16 text model of the catalogue")
    model = None
    if new.register_model:
        try:
            model = runs.model_id(new.name)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        # A run that failed or was cancelled registered nothing; its name can be used again
        taken = db.query(Run).filter(Run.model_id == model, Run.status.notin_(runs.UNSUCCESSFUL)).first()
        if taken or any(m["id"] == model for m in await _catalog(token)):
            raise HTTPException(status_code=409, detail=f"the model {model} exists already; give the run another name")
    run = Run(id=str(uuid.uuid4()), name=new.name, dataset_id=new.dataset_id, dataset_version=dataset["version"],
              dataset=dataset, evaluation=evaluation, milestones=milestones,
              base_model=new.base_model,
              method=new.method, node=new.node, register=new.register_model, model_id=model, job=job,
              when=new.when, across_slots=new.across_slots, queue_on_plan=new.queue, memory_gb=new.memory_gb,
              priority=new.priority if new.priority is not None else priorities.table(db)["forge_run"],
              cpu_cores=new.cpu_cores, status="preprocessing", created_by=user.preferred_username)
    try:
        run.mlflow_experiment_id, run.mlflow_run_id = await runfiles.create(run.name, run.id, dataset)
    except runfiles.RunFilesError as e:
        raise HTTPException(status_code=502, detail=str(e))
    db.add(run)
    db.commit()
    db.refresh(run)
    return _run_dict(run)


def _run(db: Session, run_id: str) -> Run:
    run = db.query(Run).filter(Run.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


async def _refresh_export(db: Session, run: Run, token: str) -> None:
    """The exports' job states, read again from Control while they are open. A failed NVFP4 export
    keeps what its cell raised, such as Control's refusal to publish it."""
    for field in ("export", "nvfp4"):
        exported = getattr(run, field)
        if not exported or exported["status"] in runs.JOB_FINAL:
            continue
        try:
            job = await control.job(token, exported["job_id"])
        except control.ControlError as e:
            raise _control_failed(e)
        error = worker.raised(job) if job["status"] == "error" else job.get("error")
        setattr(run, field, {**exported, "status": job["status"], "error": error})
    db.commit()
    db.refresh(run)


@router.get("/runs", operation_id="forge_list_runs")
async def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every run, newest first, as the queue worker last recorded it."""
    return [_run_dict(r) for r in db.query(Run).order_by(Run.created_at.desc()).all()]


@router.get("/runs/{run_id}", operation_id="forge_get_run")
async def get_run(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                  db: Session = Depends(get_db)):
    """The run, with its notebook job now: the preprocessing job while its data is preprocessed, the plan job
    while it plans, else its training's."""
    run = _run(db, run_id)
    await _refresh_export(db, run, token)
    job_id = ((run.preprocess or {}).get("job_id") if run.status == "preprocessing"
              else run.plan_job_id if run.status == "planning" else run.job_id)
    job = None
    if job_id:
        try:
            job = await control.job(token, job_id)
        except control.ControlError as e:
            raise _control_failed(e)
    return {**_run_dict(run), "job": job}


@router.get("/runs/{run_id}/progress", operation_id="forge_run_progress")
async def run_progress(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The run's metrics from MLflow; null while the training has not begun."""
    run = _run(db, run_id)
    try:
        found = await mlflow.progress(run.id)
    except mlflow.MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if found and run.mlflow_run_id != found["mlflow_run_id"]:
        run.mlflow_run_id = found["mlflow_run_id"]
        db.commit()
    return found


class Replan(BaseModel):
    decisions: Dict[str, Any] = Field({}, description=DECISIONS)
    milestones: Optional[Milestones] = Field(None, description="the run's milestones, replacing its earlier ones; "
                                                               "null removes them; the earlier ones stay when not "
                                                               "given")


@router.post("/runs/{run_id}/plan", operation_id="forge_plan_again")
async def replan(run_id: str, request: Replan, user: User = Depends(get_current_user),
                 token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Plan the run again with the decisions given, which replace the run's earlier ones; Meep² reports each one
    as the person's. The data is preprocessed again first (found at once when nothing that made it changed).
    The new plan is in the run once its status is planned again."""
    run = _run(db, run_id)
    if run.job_id is not None or run.status not in ("planned", "queued", "failed"):
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; only a run that has not trained "
                                                    "is planned again")
    milestones = await _milestones(token, request.milestones) if "milestones" in request.model_fields_set \
        else run.milestones
    try:
        run.job = runs.meepsquared_job(run.method, run.job.get("steps"), run.job.get("epochs"), request.decisions,
                                       run.evaluation, milestones)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    run.milestones = milestones
    run.queue_on_plan = run.status == "queued"
    run.status, run.plan, run.plan_job_id, run.preprocess, run.position, run.error, run.finished_at = \
        "preprocessing", None, None, None, None, None, None
    db.commit()
    db.refresh(run)
    return _run_dict(run)


class QueueRequest(BaseModel):
    when: Optional[Literal["now", "slots"]] = None
    across_slots: Optional[bool] = None
    priority: Optional[int] = Field(None, ge=1, le=5)


@router.post("/runs/{run_id}/queue", operation_id="forge_queue_run")
async def queue_run(run_id: str, request: QueueRequest, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Put a planned run at the end of its node's queue, or change when a waiting run may start and its priority."""
    run = _run(db, run_id)
    if request.priority is not None:
        if run.status not in runs.BEFORE_PLAN + ("planned",) + runs.WAITING:
            raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; its priority is changed "
                                                        "while it waits, queued or paused")
        run.priority = request.priority
    if request.when is not None:
        run.when = request.when
    if request.across_slots is not None:
        run.across_slots = request.across_slots
    if run.when == "now" and run.across_slots:
        raise HTTPException(status_code=422, detail="across_slots is for a run that waits for slots")
    if run.status == "planned":
        if not runs.sized(run):
            raise HTTPException(status_code=409, detail=f"run {run.name} cannot be queued: Meep²'s plan does not size "
                                                        f"its training job ({runs.memory_reason(run.plan)}); set its "
                                                        "memory under Advanced")
        worker.enqueue(db, run)
    elif run.status in runs.BEFORE_PLAN:
        run.queue_on_plan = True
    elif run.status not in runs.WAITING:
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; it cannot be queued")
    db.commit()
    db.refresh(run)
    return _run_dict(run)


class PriorityRequest(BaseModel):
    priority: int = Field(..., ge=1, le=5)


@router.post("/runs/{run_id}/priority", operation_id="forge_set_run_priority")
async def set_run_priority(run_id: str, request: PriorityRequest, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """A waiting run's priority, 1 (highest) to 5 (lowest); the worker decides with it at its next tick."""
    run = _run(db, run_id)
    if run.status not in runs.BEFORE_PLAN + ("planned",) + runs.WAITING:
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; its priority is changed "
                                                    "while it waits, queued or paused")
    run.priority = request.priority
    db.commit()
    db.refresh(run)
    return _run_dict(run)


class ResourcesRequest(BaseModel):
    # Advanced: the person's memory and CPU cores for the run's jobs; null gives them back to auto mode
    memory_gb: Optional[int] = Field(None, ge=4, le=1024)
    cpu_cores: Optional[int] = Field(None, ge=1, le=256)


@router.post("/runs/{run_id}/resources", operation_id="forge_set_run_resources")
async def set_run_resources(run_id: str, request: ResourcesRequest, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """A waiting run's memory and CPU cores, the person's choice; its next job asks for them."""
    run = _run(db, run_id)
    if run.status not in runs.BEFORE_PLAN + ("planned",) + runs.WAITING:
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; its memory and cores are "
                                                    "changed while it waits, queued or paused")
    run.memory_gb, run.cpu_cores = request.memory_gb, request.cpu_cores
    db.commit()
    db.refresh(run)
    return _run_dict(run)


@router.post("/runs/{run_id}/pause", operation_id="forge_pause_run")
async def pause_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hold a waiting run until it is resumed; a running one stops at its next step and keeps its checkpoint."""
    run = _run(db, run_id)
    if run.status in ("queued", "paused"):
        run.status, run.paused_by = "paused", "user"
    elif run.status == "running":
        try:
            await runfiles.RunFiles().request_stop(run)
        except runfiles.RunFilesError as e:
            raise HTTPException(status_code=502, detail=str(e))
        run.status, run.paused_by, run.stop_requested_at = "stopping", "user", worker.utcnow()
    elif run.status == "stopping":
        run.paused_by = "user"
    else:
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; it cannot be paused")
    db.commit()
    db.refresh(run)
    return _run_dict(run)


@router.post("/runs/{run_id}/resume", operation_id="forge_resume_run")
async def resume_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A paused run, or a failed one, back to its node's queue; it goes on from its newest checkpoint."""
    run = _run(db, run_id)
    if run.status == "paused":
        run.status, run.paused_by, run.preempted_by = "queued", None, None
    elif run.status == "failed" and run.plan is not None:
        worker.enqueue(db, run)
        run.error, run.finished_at = None, None
    else:
        raise HTTPException(status_code=409, detail=f"run {run.name} is {run.status}; only a paused or failed run is resumed")
    db.commit()
    db.refresh(run)
    return _run_dict(run)


@router.post("/runs/{run_id}/cancel", operation_id="forge_cancel_run")
async def cancel_run(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)):
    """End a run that has not ended: its job, when one holds the node, is cancelled at Control."""
    run = _run(db, run_id)
    if run.status in runs.ENDED:
        return _run_dict(run)
    open_job = (run.job_id if run.status in runs.TRAINING else run.plan_job_id if run.status == "planning"
                else (run.preprocess or {}).get("job_id") if run.status == "preprocessing" else None)
    if open_job:
        try:
            await control.cancel_job(token, open_job)
        except control.ControlError as e:
            raise _control_failed(e)
    run.status, run.position, run.finished_at, run.stop_requested_at = "cancelled", None, worker.utcnow(), None
    run.waiting = None
    db.commit()
    db.refresh(run)
    return _run_dict(run)


class Order(BaseModel):
    runs: List[str]


@router.put("/queue/{node}/order", operation_id="forge_order_queue")
async def reorder(node: str, order: Order, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The order of a node's waiting runs: every queued and paused run of the node, first to last."""
    waiting = db.query(Run).filter(Run.node == node, Run.status.in_(runs.WAITING)).all()
    if sorted(order.runs) != sorted(r.id for r in waiting):
        raise HTTPException(status_code=422, detail=f"the order must name each waiting run of {node} once: "
                                                    f"{sorted(r.id for r in waiting)}")
    by_id = {r.id: r for r in waiting}
    for position, run_id in enumerate(order.runs, start=1):
        by_id[run_id].position = position
    db.commit()
    return [_run_dict(by_id[i]) for i in order.runs]


@router.get("/queue", operation_id="forge_queue")
async def queue(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Each node of the queue: its runs in order, its slot now and its next one, and the worker's note."""
    now = worker.utcnow()
    by_node: Dict[str, List[Slot]] = {}
    for s in db.query(Slot).all():
        by_node.setdefault(s.node, []).append(s)
    active = db.query(Run).filter(Run.status.in_(runs.BEFORE_PLAN + ("planned",) + runs.WAITING
                                                 + runs.TRAINING)).all()
    nodes = sorted(set(by_node) | {r.node for r in active})
    found = []
    for node in nodes:
        state = db.get(NodeState, node)
        window = slots.active(by_node.get(node, []), now)
        upcoming = slots.upcoming(by_node.get(node, []), now)
        mine = sorted((r for r in active if r.node == node),
                      key=lambda r: (r.status not in runs.TRAINING, r.position is None, r.position or 0, r.created_at))
        found.append({"node": node, "slot": window.to_dict() if window else None,
                      "next_slot": upcoming.to_dict() if upcoming else None,
                      "training_mode": bool(state and state.training_mode), "note": state.note if state else None,
                      "runs": [_run_dict(r) for r in mine]})
    return {"nodes": found, "worker": {**worker.state, "connected": worker.credential(db) is not None}}


# --- Slots -------------------------------------------------------------------

class SlotRequest(BaseModel):
    node: str = Field(..., min_length=1)
    days: List[int] = Field(..., description="the days of the week it opens, 0 (Monday) to 6 (Sunday)")
    start: str = Field(..., description="the time it opens, HH:MM")
    end: str = Field(..., description="the time it closes, HH:MM; before start, it closes the next day")
    timezone: str = Field(slots.DEFAULT_TIMEZONE, description="an IANA time zone")


def _slot_dict(db: Session, slot: Slot) -> Dict[str, Any]:
    """A slot with its node's next window, and the windows of other nodes it overlaps in the coming week."""
    now = worker.utcnow()
    mine = db.query(Slot).filter(Slot.node == slot.node).all()
    upcoming = slots.active(mine, now) or slots.upcoming(mine, now)
    others: Dict[str, List[Slot]] = {}
    for s in db.query(Slot).filter(Slot.node != slot.node).all():
        others.setdefault(s.node, []).append(s)
    overlaps = []
    for node, theirs in sorted(others.items()):
        for a, b in slots.overlapping([slot], theirs, now):
            overlaps.append({"node": node, "start": max(a.start, b.start).isoformat(), "end": min(a.end, b.end).isoformat()})
    return {**slot.to_dict(), "next_window": upcoming.to_dict() if upcoming else None, "overlaps": overlaps}


def _checked(request: SlotRequest) -> None:
    try:
        slots.check(request.days, request.start, request.end, request.timezone)
    except slots.SlotError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/slots", operation_id="forge_list_slots")
async def list_slots(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every slot: the weekly windows in which a run that waits for slots may train on its node."""
    return [_slot_dict(db, s) for s in db.query(Slot).order_by(Slot.node, Slot.start).all()]


@router.post("/slots", operation_id="forge_create_slot")
async def create_slot(request: SlotRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A new weekly window of a node, in which its runs that wait for slots may train."""
    _checked(request)
    slot = Slot(id=str(uuid.uuid4()), node=request.node, days=sorted(request.days), start=request.start,
                end=request.end, timezone=request.timezone, created_by=user.preferred_username)
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return _slot_dict(db, slot)


def _slot(db: Session, slot_id: str) -> Slot:
    slot = db.get(Slot, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"slot {slot_id} not found")
    return slot


@router.put("/slots/{slot_id}", operation_id="forge_update_slot")
async def update_slot(slot_id: str, request: SlotRequest, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """A slot changed: its node, days, times and time zone."""
    _checked(request)
    slot = _slot(db, slot_id)
    slot.node, slot.days, slot.start, slot.end, slot.timezone = \
        request.node, sorted(request.days), request.start, request.end, request.timezone
    db.commit()
    db.refresh(slot)
    return _slot_dict(db, slot)


@router.delete("/slots/{slot_id}", operation_id="forge_delete_slot")
async def delete_slot(slot_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove a slot. A window that is open ends with it: the worker pauses its runs at the next tick."""
    db.delete(_slot(db, slot_id))
    db.commit()
    return {"deleted": slot_id}


# --- Priorities of the work on a GPU node ----------------------------------

def _priorities(db: Session) -> List[Dict[str, Any]]:
    table = priorities.table(db)
    return [{"kind": kind, "label": label, "default": default, "priority": table[kind],
             "stopped_by": priorities.STOPPED_BY[kind]} for kind, (label, default) in priorities.KINDS.items()]


@router.get("/priorities", operation_id="forge_get_priorities")
async def get_priorities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The priority of each kind of work on a GPU node, 1 (highest) to 5 (lowest), and how each is stopped.
    forge_run is the priority a new run gets when none is given."""
    return _priorities(db)


@router.put("/priorities")
async def put_priorities(changes: Dict[str, int], user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        priorities.set_priorities(db, changes)
    except priorities.PriorityError as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return _priorities(db)


# --- The queue worker's token for Thinkube Control ---------------------------

@router.get("/worker", operation_id="forge_worker_status")
async def worker_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The queue worker: whether it holds a Control token to start jobs with, whose, and its last tick."""
    found = worker.credential(db)
    return {**worker.state, "connected": found is not None, "username": found.username if found else None,
            "connected_at": found.created_at.isoformat() if found else None}


@router.post("/worker/connect")
async def connect_worker(user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                         db: Session = Depends(get_db)):
    """Give the queue worker a Control API token of the signed-in person, made by Control with their session.
    The worker starts jobs and turns training mode on and off with it, with that person's rights."""
    if worker.credential(db) is not None:
        raise HTTPException(status_code=409, detail="the queue is connected already; disconnect it first")
    try:
        made = await control.create_api_token(token, "Forge training queue")
    except control.ControlError as e:
        raise _control_failed(e)
    db.add(WorkerCredential(id=worker.CREDENTIAL_ID, token=made["token"], token_id=made["id"],
                            username=user.preferred_username))
    db.commit()
    return await worker_status(user, db)


@router.delete("/worker/connect")
async def disconnect_worker(user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                            db: Session = Depends(get_db)):
    """Revoke the worker's Control token; the queue stops starting jobs until it is connected again."""
    found = worker.credential(db)
    if found is None:
        raise HTTPException(status_code=409, detail="the queue is not connected")
    try:
        await control.revoke_api_token(token, found.token_id)
    except control.ControlError as e:
        raise _control_failed(e)
    db.delete(found)
    db.commit()
    return await worker_status(user, db)


def _model_of(run: Run) -> str:
    if not run.model_id:
        raise HTTPException(status_code=409, detail=f"run {run.name} does not register a model")
    return run.model_id


@router.get("/runs/{run_id}/model")
async def run_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                    db: Session = Depends(get_db)):
    """The state of the run's model in the LLM Gateway."""
    try:
        return await control.model_status(token, _model_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)


class LoadRequest(BaseModel):
    node: Optional[str] = None


@router.post("/runs/{run_id}/model/load")
async def load_run_model(run_id: str, request: LoadRequest, user: User = Depends(get_current_user),
                         token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        return await control.load_model(token, _model_of(_run(db, run_id)), request.node)
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/model/unload")
async def unload_run_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                           db: Session = Depends(get_db)):
    try:
        return await control.unload_model(token, _model_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)


@router.get("/runs/{run_id}/base")
async def base_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)):
    """The state of the run's base model in the LLM Gateway, to compare the two."""
    try:
        return await control.model_status(token, _run(db, run_id).base_model)
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/base/load")
async def load_base_model(run_id: str, request: LoadRequest, user: User = Depends(get_current_user),
                          token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        return await control.load_model(token, _run(db, run_id).base_model, request.node)
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/base/unload")
async def unload_base_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                            db: Session = Depends(get_db)):
    try:
        return await control.unload_model(token, _run(db, run_id).base_model)
    except control.ControlError as e:
        raise _control_failed(e)


class Turn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., max_length=100_000)


class Chat(BaseModel):
    target: Literal["base", "tuned", "gguf", "nvfp4"]
    messages: List[Turn] = Field(..., min_length=1, max_length=200)
    max_tokens: int = Field(512, ge=1, le=8192)
    temperature: float = Field(0.7, ge=0, le=2)


@router.post("/runs/{run_id}/chat")
async def chat(run_id: str, request: Chat, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
               db: Session = Depends(get_db)):
    """An answer of the fine-tuned model or of its base model, through the LLM Gateway."""
    run = _run(db, run_id)
    model = {"tuned": lambda: _model_of(run), "base": lambda: run.base_model, "gguf": lambda: _gguf_of(run),
             "nvfp4": lambda: _nvfp4_of(run)}[request.target]()
    try:
        return await gateway.chat(token, model, [t.model_dump() for t in request.messages],
                                  request.max_tokens, request.temperature)
    except gateway.GatewayError as e:
        raise HTTPException(status_code=e.status if 400 <= e.status < 500 else 502, detail=e.detail)


def _gguf_of(run: Run) -> str:
    if not run.export or run.export["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"run {run.name} has no completed GGUF export")
    return run.export["model_id"]


class ExportRequest(BaseModel):
    quantization: Literal["Q8_0", "F16"] = "Q8_0"
    node: str


@router.post("/runs/{run_id}/export")
async def export_run(run_id: str, request: ExportRequest, user: User = Depends(get_current_user),
                     token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Convert the run's registered model to a GGUF file, served by llama.cpp: a notebook job on the node, without a GPU."""
    run = _run(db, run_id)
    if not (run.register and run.status == "completed"):
        raise HTTPException(status_code=409, detail=f"run {run.name} has no registered model to export")
    await _refresh_export(db, run, token)
    if run.export and run.export["status"] not in runs.UNSUCCESSFUL_JOB:
        raise HTTPException(status_code=409, detail=f"run {run.name} is exported already, or its export is running")
    base = next((m for m in await _catalog(token) if m["id"] == run.base_model), None)
    if base is None:
        raise HTTPException(status_code=409, detail=f"the base model {run.base_model} is no longer in the catalogue")
    try:
        job = await control.run_job(token, runs.export_request(run, base, request.quantization, request.node))
    except control.ControlError as e:
        raise _control_failed(e)
    run.export = {"model_id": f"{run.model_id}-gguf", "quantization": request.quantization, "node": request.node,
                  "job_id": job["job_id"], "status": job["status"], "error": job.get("error")}
    db.commit()
    db.refresh(run)
    return run.to_dict()


@router.get("/runs/{run_id}/gguf")
async def gguf_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)):
    """The state of the run's GGUF model in the LLM Gateway."""
    try:
        return await control.model_status(token, _gguf_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/gguf/load")
async def load_gguf_model(run_id: str, request: LoadRequest, user: User = Depends(get_current_user),
                          token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        return await control.load_model(token, _gguf_of(_run(db, run_id)), request.node)
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/gguf/unload")
async def unload_gguf_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                            db: Session = Depends(get_db)):
    try:
        return await control.unload_model(token, _gguf_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)


# --- The NVFP4 export: served by vLLM on Blackwell GPUs --------------------

def _nvfp4_of(run: Run) -> str:
    if not run.nvfp4 or run.nvfp4["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"run {run.name} has no completed NVFP4 export")
    return run.nvfp4["model_id"]


class Nvfp4Request(BaseModel):
    node: str
    calibration_rows: int = Field(runs.NVFP4_CALIBRATION_ROWS, ge=16, le=8192)


@router.post("/runs/{run_id}/export/nvfp4")
async def export_nvfp4(run_id: str, request: Nvfp4Request, user: User = Depends(get_current_user),
                       token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Quantize the run's registered model to NVFP4, calibrated on rows of the split it trained on, and
    publish it for vLLM: a notebook job with one GPU of a node that computes NVFP4."""
    run = _run(db, run_id)
    if not (run.register and run.status == "completed"):
        raise HTTPException(status_code=409, detail=f"run {run.name} has no registered model to export")
    await _refresh_export(db, run, token)
    if run.nvfp4 and run.nvfp4["status"] not in runs.UNSUCCESSFUL_JOB:
        raise HTTPException(status_code=409, detail=f"run {run.name} is exported to NVFP4 already, or its export is running")
    if not run.dataset:
        raise HTTPException(status_code=409, detail=f"run {run.name} records no dataset to calibrate on")
    try:
        status = await control.gpu_status(token)
    except control.ControlError as e:
        raise _control_failed(e)
    node = next((n for n in status["nodes"] if n["name"] == request.node), None)
    if node is None:
        raise HTTPException(status_code=422, detail=f"{request.node} is not a GPU node of the cluster")
    if not runs.nvfp4_capable(node.get("compute_capability")):
        raise HTTPException(status_code=422, detail=(
            f"{request.node} has a GPU of compute capability {node.get('compute_capability') or 'unknown'} "
            f"({node.get('gpu_product') or 'GPU'}); NVFP4 is computed by GPUs of compute capability "
            f"{runs.NVFP4_MIN_CAPABILITY[0]}.{runs.NVFP4_MIN_CAPABILITY[1]} or higher (Blackwell)"))
    busy = worker.Worker._forge_work(db, request.node)
    if busy:
        raise HTTPException(status_code=409, detail=f"{request.node} holds run {busy[0].name} on its GPU; export when "
                                                    "it is free")
    try:
        found = next((v for v in await control.venvs(token) if v["name"] == runs.NVFP4_KERNEL), None)
    except control.ControlError as e:
        raise _control_failed(e)
    if found is None or found.get("status") != "success":
        state = "is not built" if found else "does not exist"
        raise HTTPException(status_code=409, detail=(
            f"the NVFP4 export needs the Python environment {runs.NVFP4_KERNEL}, which {state}: create it in "
            f"Thinkube Control (Jupyter environments) with {', '.join(runs.NVFP4_PACKAGES)}"))
    base = next((m for m in await _catalog(token) if m["id"] == run.base_model), None)
    if base is None:
        raise HTTPException(status_code=409, detail=f"the base model {run.base_model} is no longer in the catalogue")
    try:
        job = await control.run_job(token, runs.nvfp4_request(run, base, request.node, request.calibration_rows))
    except control.ControlError as e:
        raise _control_failed(e)
    run.nvfp4 = {"model_id": f"{run.model_id}-nvfp4", "calibration_rows": request.calibration_rows,
                 "node": request.node, "job_id": job["job_id"], "status": job["status"], "error": job.get("error")}
    db.commit()
    db.refresh(run)
    return _run_dict(run)


@router.get("/runs/{run_id}/nvfp4")
async def nvfp4_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                      db: Session = Depends(get_db)):
    """The state of the run's NVFP4 model in the LLM Gateway."""
    try:
        return await control.model_status(token, _nvfp4_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/nvfp4/load")
async def load_nvfp4_model(run_id: str, request: LoadRequest, user: User = Depends(get_current_user),
                           token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Load the NVFP4 model; the gateway places it only on GPUs that compute NVFP4."""
    try:
        return await control.load_model(token, _nvfp4_of(_run(db, run_id)), request.node)
    except control.ControlError as e:
        raise _control_failed(e)


@router.post("/runs/{run_id}/nvfp4/unload")
async def unload_nvfp4_model(run_id: str, user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
                             db: Session = Depends(get_db)):
    try:
        return await control.unload_model(token, _nvfp4_of(_run(db, run_id)))
    except control.ControlError as e:
        raise _control_failed(e)
