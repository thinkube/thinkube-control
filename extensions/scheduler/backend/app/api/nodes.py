# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The nodes and their GPUs."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import User, get_current_user
from app.services import nodes

router = APIRouter()


@router.get("/nodes", operation_id="scheduler_list_nodes")
async def list_nodes(user: User = Depends(get_current_user)):
    """The cluster's nodes: each one's kind ("separate": each GPU has memory of its own; "shared": the GPU takes its
    memory from the system's, so all the work on the node shares one memory; "none": no GPU), its memory, and its
    GPUs by id with what each one holds now. A node that could not be read has "error" in place of its figures."""
    try:
        return {"nodes": await nodes.nodes.list()}
    except nodes.NodesError as e:
        raise HTTPException(status_code=502, detail=str(e))
