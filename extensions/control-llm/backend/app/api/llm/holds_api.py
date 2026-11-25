# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""What the Scheduler asks of the gateway about the GPUs it holds for the gateway's pods."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import gpu_holds
from app.services.llm_lifecycle import llm_lifecycle

router = APIRouter()

# The word reaches the route as a JSON key, which a Python field cannot be named
FOR = "for"


class GiveUp(BaseModel):
    hold: str = Field(..., description="the id of the hold whose GPUs are asked for")
    name: str = Field(..., description="the hold's name: the id of the model its pod serves")
    needed_for: str = Field(..., alias=FOR, description="the job that needs the GPUs")


@router.post("/release")
async def give_up(request: GiveUp):
    """The Scheduler asks for the GPUs of a model's pod, for work of a higher priority. The model is unloaded, and
    loaded again when the Scheduler gives its hold GPUs again. Asked again meanwhile, it answers the same."""
    try:
        return {"done": await llm_lifecycle.give_up(request.hold, request.name, request.needed_for)}
    except gpu_holds.HoldError as e:
        raise HTTPException(status_code=502, detail=str(e))
