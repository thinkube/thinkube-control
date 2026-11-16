# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The control center: the runs of Mill tasks, their progress, samples,
failures and logs, and starting and stopping a run in the main pod."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.core.security import User, get_current_user
from app.db.session import get_db
from app.services import consumer, runs

router = APIRouter()


class StartRequest(BaseModel):
    file: str = Field(..., description="The task's file, relative to the root of the tasks repository, "
                                       "for example 'kubexlat/clean.py'.")
    task: Optional[str] = Field(None, description="The task's name, when the file declares several.")
    inputs: Dict[str, str] = Field(default_factory=dict, description="The path of each input, by its name; "
                                   "relative to the root of the tasks repository, or absolute.")
    outputs: Dict[str, str] = Field(default_factory=dict, description="The path of each output, by its name; "
                                    "relative to the root of the tasks repository, or absolute.")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Values for the task's settings, in "
                                     "place of their defaults.")
    limit: Optional[int] = Field(None, ge=1, description="Read only the first N items of each jsonl input: "
                                 "a way to try a task in seconds.")


def _do(fn, *args):
    try:
        return fn(*args)
    except runs.RunNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except runs.RunsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/runs", operation_id="mill_list_runs")
def list_runs(
    limit: int = Query(50, ge=1, le=500, description="How many runs to answer, the newest first."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The newest runs of Mill tasks, wherever they execute: each with its task, its state (running,
    completed, failed, stopped), its stages (name, total, done), its numbers of samples and failures, the
    host it runs on, and the commit of the tasks repository it ran from (dirty when it had changes not
    committed). problem says why the control center receives nothing from the runs, when it does not;
    the runs listed are then as they were when it last did."""
    answer = _do(runs.list_runs, db, limit)
    answer["problem"] = consumer.consumer.problem if consumer.consumer is not None else None
    return answer


@router.get("/runs/{run_id}", operation_id="mill_get_run")
def get_run(run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """One run: what was run (task, file, inputs, outputs, settings, commit, host) and its status (state,
    error, stages, counters, measures, numbers of samples and failures). can_stop says whether
    mill_stop_run can stop it; has_log whether mill_get_run_log has its log."""
    return _do(runs.get_run, db, run_id)


@router.get("/runs/{run_id}/samples", operation_id="mill_get_run_samples")
def get_run_samples(run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Samples of the items a run processed: the latest 10, and up to 20 spaced across the run. Each has
    its number and the fields the task showed, usually input and output. Long text is cut at 2000
    characters."""
    return _do(runs.samples, db, run_id)


@router.get("/runs/{run_id}/failures", operation_id="mill_get_run_failures")
def get_run_failures(
    run_id: str,
    limit: int = Query(50, ge=1, le=200, description="How many failed items to answer, the first ones first."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The items a run reported as failed, each with the error and the fields the task gave. total counts
    them all; the record keeps the first 200."""
    return _do(runs.failures, db, run_id, limit)


@router.get("/runs/{run_id}/log", operation_id="mill_get_run_log")
def get_run_log(
    run_id: str,
    lines: int = Query(200, ge=1, le=5000, description="How many of the last lines to answer."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The last lines a run printed, wherever it executes."""
    return _do(runs.log, db, run_id, lines)


@router.get("/tasks", operation_id="mill_describe_tasks")
def describe_tasks(
    file: str = Query(..., description="A Python file, relative to the root of the tasks repository."),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """The tasks a file of the tasks repository declares: each with its name, description, inputs and
    outputs (name and type: jsonl, json, text or path) and settings with their defaults."""
    return _do(runs.describe, file)


@router.post("/runs", operation_id="mill_start_run")
def start_run(request: StartRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Start a run of a task in the control center's pod, which has no GPU: for work that calls models
    through the platform's services, cleans data or scores results. Answers the run's id once the run's
    first status is stored; follow it with mill_get_run and mill_get_run_samples. A task that cannot
    start (a missing input, an unknown setting) is refused with the reason."""
    return _do(runs.start, db, request.file, request.task, request.inputs, request.outputs, request.settings,
               request.limit)


@router.post("/runs/{run_id}/stop", operation_id="mill_stop_run")
async def stop_run(run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Stop a running run, wherever it executes: the control center asks the run's process through NATS,
    and the task ends at once with the state stopped; its outputs stay as partial files. Refused when the
    run is not running, or when no process answers for it."""
    state = _do(runs.get_run, db, run_id)["status"]["state"]
    if state != "running":
        raise HTTPException(status_code=409, detail=f"run {run_id} is {state}; only a running run is stopped")
    if consumer.consumer is None:
        raise HTTPException(status_code=503, detail="the control center's connection to NATS is not started")
    try:
        answered = await consumer.consumer.request_stop(run_id)
    except consumer.NotConnected as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not answered:
        raise HTTPException(
            status_code=409,
            detail=f"no process answers for run {run_id}: it has ended. The control center marks a run "
                   "that sends nothing for 90 seconds as failed.",
        )
    return {"id": run_id, "asked": "stop"}
