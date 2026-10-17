# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Training mode: the whole cluster reserved for one training."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.api_tokens import get_current_user_dual_auth
from app.services import training_mode

router = APIRouter()


class TrainingModeOnRequest(BaseModel):
    node: str = Field(..., description="The node the training runs on, for example 'tkspark'.")
    reason: str = Field(..., description="What the training is, kept in the record and shown while it is on.")


@router.get("/training-mode", operation_id="get_training_mode")
async def get_training_mode(current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """Whether training mode is on, and its record: the node, the reason, when it started, the models it
    unloaded and every workload it scaled to 0 with the replicas it had."""
    record: Optional[Dict[str, Any]] = await training_mode.status()
    return {"on": record is not None, "record": record}


@router.post("/training-mode/on", operation_id="training_mode_on")
async def training_mode_on(
    request: TrainingModeOnRequest, current_user: dict = Depends(get_current_user_dual_auth)
) -> Dict[str, Any]:
    """Reserve the whole cluster for a training on one node: unload every served model, scale to 0 every
    Deployment and StatefulSet outside the namespaces a training needs (cluster, storage, JupyterHub,
    Thinkube Control, PostgreSQL, Keycloak, Harbor, gateways, Tailscale, code-server, scoring), keep new
    pods off the node and move the pods of kept Deployments off it. Data stays where it is. Takes about a
    minute. Refused while training mode is already on."""
    try:
        return await training_mode.turn_on(request.node, request.reason)
    except training_mode.TrainingModeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/training-mode/off", operation_id="training_mode_off")
async def training_mode_off(current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """End training mode: restore every workload it scaled to 0 to the replicas it had, open the node again
    and load again the models it unloaded, on the nodes they were on, one after another (models_loading;
    the loads run on after the answer, follow them with get_llm_model_status). A workload changed since is
    left as it is and named in not_restored. Refused when training mode is not on."""
    try:
        return await training_mode.turn_off()
    except training_mode.TrainingModeError as e:
        raise HTTPException(status_code=409, detail=str(e))
