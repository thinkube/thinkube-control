# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The priority of each kind of work on the nodes, 1 (highest) to 5 (lowest), which a person sets.

Each part of the platform that gives the scheduler work reads the priority of its kind here when it gives it: a
notebook server and an unattended notebook run (JupyterHub, for Thinkube Control), a Forge run when the person
gives it none, a model the LLM gateway serves, and an app with a GPU (Thinkube Control). Work of a higher priority
takes the GPUs from work of a lower one; work of the same priority waits its turn.
"""

from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from app.models.priorities import Priority

# Each kind: how the pages name it, its default, and how its work gives its GPUs up for work of a higher priority
KINDS: Dict[str, Tuple[str, int, str]] = {
    "notebook_server": ("Notebook server", 1, "JupyterHub stops nothing for it to give up: higher work waits for it"),
    "notebook_job": ("Unattended notebook run", 2, "its pod is stopped, and the run fails"),
    "forge_run": ("Forge run", 3, "its pod is stopped, and the training goes on from its last checkpoint when its "
                                  "turn comes"),
    "served_model": ("Served model", 4, "the LLM gateway unloads it, and loads it again when its GPUs are free"),
    "app": ("App with a GPU", 5, "Thinkube Control stops its pods, and starts them again when its GPUs are free"),
}
HIGHEST, LOWEST = 1, 5


class PriorityError(ValueError):
    """A priority or a kind that does not exist; the message says which."""


def table(db: Session) -> Dict[str, int]:
    """The priority of each kind, as the person set it, else its default."""
    found = {p.kind: p.priority for p in db.query(Priority).all()}
    return {kind: found.get(kind, default) for kind, (_, default, _) in KINDS.items()}


def listed(db: Session) -> List[Dict[str, Any]]:
    current = table(db)
    return [{"kind": kind, "label": label, "default": default, "priority": current[kind], "gives_up": gives_up}
            for kind, (label, default, gives_up) in KINDS.items()]


def set_priorities(db: Session, changes: Dict[str, Any]) -> None:
    unknown = sorted(set(changes) - set(KINDS))
    if unknown:
        raise PriorityError(f"{unknown} are not kinds of work; they are {sorted(KINDS)}")
    for kind, priority in changes.items():
        if isinstance(priority, bool) or not isinstance(priority, int) or not HIGHEST <= priority <= LOWEST:
            raise PriorityError(f"a priority is a whole number from {HIGHEST} (highest) to {LOWEST} (lowest), not "
                                f"{priority!r}")
        row = db.get(Priority, kind)
        if row is None:
            db.add(Priority(kind=kind, priority=priority))
        else:
            row.priority = priority
