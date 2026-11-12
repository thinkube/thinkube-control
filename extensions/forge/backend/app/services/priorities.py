# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Priorities of the work on a GPU node: 1 (highest) to 5 (lowest).

When a Forge run wants a node's GPU, every piece of work on the node is
compared with it: work of a higher priority (a lower number) or of the same
priority makes the run wait; work of a lower priority is stopped to make room,
each kind in the gentlest way it has (STOPPED_BY). A Forge run has its own
priority; work outside Forge has the priority Forge keeps for its kind, in the
priorities table, which a person edits.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy.orm import Session

from app.models.forge import Priority

# Each kind: how the pages name it, and the default the person decided
KINDS: Dict[str, Tuple[str, int]] = {
    "notebook_server": ("notebook server", 1),
    "notebook_job": ("notebook job", 2),
    "forge_run": ("run", 3),
    "served_model": ("served model", 4),
    "app": ("app", 5),
}
STOPPED_BY = {
    "forge_run": "paused at its next step, with a checkpoint (its stop file)",
    "notebook_job": "cancelled through Control",
    "notebook_server": "stopped through Control",
    "served_model": "unloaded by Control's training mode, and loaded again when it ends",
    "app": "scaled to 0 by Control's training mode, and restored when it ends",
}
# The kinds Control's training mode stops
BY_TRAINING_MODE = ("served_model", "app")
HIGHEST, LOWEST = 1, 5


class PriorityError(ValueError):
    """A priority or a kind that does not exist; the message says which."""


def check(priority: Any) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int) or not HIGHEST <= priority <= LOWEST:
        raise PriorityError(f"a priority is a whole number from {HIGHEST} (highest) to {LOWEST} (lowest), not {priority!r}")
    return priority


def table(db: Session) -> Dict[str, int]:
    """The priority of each kind, as the person set it."""
    found = {p.kind: p.priority for p in db.query(Priority).all()}
    missing = sorted(set(KINDS) - set(found))
    if missing:
        raise PriorityError(f"the priorities table has no row for {missing}; the migration e5a1c7b9d3f2 writes them")
    return found


def set_priorities(db: Session, changes: Dict[str, int]) -> Dict[str, int]:
    unknown = sorted(set(changes) - set(KINDS))
    if unknown:
        raise PriorityError(f"{unknown} are not kinds of work; they are {sorted(KINDS)}")
    for kind, priority in changes.items():
        db.get(Priority, kind).priority = check(priority)
    db.flush()
    return table(db)


@dataclass
class Occupant:
    """A piece of work on a node: its kind, its name, its priority, and what stops it."""

    kind: str
    name: str
    priority: int
    ref: Any = None

    @property
    def label(self) -> str:
        return KINDS[self.kind][0]


def decide(priority: int, occupants: Iterable[Occupant]) -> Tuple[List[Occupant], List[Occupant]]:
    """(the work that makes a run of this priority wait, the work stopped to make room for it).

    Higher and equal priorities make it wait, highest first; lower ones are stopped."""
    occupants = list(occupants)
    waits = sorted((o for o in occupants if o.priority <= priority), key=lambda o: o.priority)
    stops = [o for o in occupants if o.priority > priority]
    return waits, stops


def waiting_for(occupant: Occupant, node: str) -> str:
    return f"waiting for {occupant.label} {occupant.name} on {node}, priority {occupant.priority}"
