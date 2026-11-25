# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The nodes and their GPUs, and the limits of a node."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import User, get_current_user
from app.db.session import get_db
from app.models.jobs import NodeLimit
from app.services import nodes

router = APIRouter()


class Limit(BaseModel):
    gpus_at_work: Optional[int] = Field(..., ge=1, le=64, description="the most GPUs of the node that hold work at "
                                        "once; null removes the limit")


@router.get("/nodes", operation_id="scheduler_list_nodes")
async def list_nodes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The cluster's nodes: each one's kind ("separate": each GPU has memory of its own; "shared": the GPU takes its
    memory from the system's, so all the work on the node shares one memory; "none": no GPU), its memory, its GPUs
    by id with what each one holds now, and its limit, gpus_at_work, when it has one. A node that could not be read
    has "error" in place of its figures."""
    try:
        found = await nodes.nodes.list()
    except nodes.NodesError as e:
        raise HTTPException(status_code=502, detail=str(e))
    limits = {limit.node: limit.gpus_at_work for limit in db.query(NodeLimit).all()}
    return {"nodes": [{**node, "gpus_at_work": limits.get(node["name"])} for node in found]}


@router.put("/nodes/{node}/limit", operation_id="scheduler_set_node_limit")
async def set_node_limit(node: str, request: Limit, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """A limit of a node that is not a priority, such as a limit of heat: the most of its GPUs that hold work at
    once. A job is not given a GPU that holds no work when the limit is reached; work that is running is not
    stopped for it."""
    limit = db.get(NodeLimit, node)
    if request.gpus_at_work is None:
        if limit is not None:
            db.delete(limit)
    elif limit is None:
        db.add(NodeLimit(node=node, gpus_at_work=request.gpus_at_work))
    else:
        limit.gpus_at_work = request.gpus_at_work
    db.commit()
    return {"node": node, "gpus_at_work": request.gpus_at_work}
