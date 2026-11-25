# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The priority of each kind of work on the nodes."""

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import User, get_current_user
from app.db.session import get_db
from app.services import priorities

router = APIRouter()


@router.get("/priorities", operation_id="scheduler_get_priorities")
async def get_priorities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The priority of each kind of work, 1 (highest) to 5 (lowest), its default, and how its work gives its GPUs
    up. Each part of the platform reads its kind's priority when it gives the scheduler work; forge_run is the
    priority of a Forge run given none."""
    return priorities.listed(db)


@router.put("/priorities", operation_id="scheduler_set_priorities")
async def set_priorities(changes: Dict[str, int], user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """Set the priority of kinds of work, such as {"served_model": 3}. Work given before keeps its priority; work
    given from now on gets the new one."""
    try:
        priorities.set_priorities(db, changes)
    except priorities.PriorityError as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return priorities.listed(db)
