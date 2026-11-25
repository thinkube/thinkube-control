# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Priorities of Forge's runs: 1 (highest) to 5 (lowest).

A run's priority goes with its jobs to the Scheduler, which weighs them against all the other work on the node.
Among Forge's own runs on a node, work of a higher or the same priority makes a run wait, and a run of a lower
priority is paused at its next step, with a checkpoint (its stop file), to make room. A run given no priority gets
the Scheduler's priority of Forge runs (scheduled.priority), which a person sets on the Scheduler's Priorities page.
"""

from dataclasses import dataclass
from typing import Any, Iterable, List, Tuple

HIGHEST, LOWEST = 1, 5


class PriorityError(ValueError):
    """A priority or a kind that does not exist; the message says which."""


def check(priority: Any) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int) or not HIGHEST <= priority <= LOWEST:
        raise PriorityError(f"a priority is a whole number from {HIGHEST} (highest) to {LOWEST} (lowest), not {priority!r}")
    return priority


@dataclass
class Occupant:
    """A run of Forge's on a node: its name, its priority, and the run."""

    kind: str
    name: str
    priority: int
    ref: Any = None

    @property
    def label(self) -> str:
        return "run"


def decide(priority: int, occupants: Iterable[Occupant]) -> Tuple[List[Occupant], List[Occupant]]:
    """(the work that makes a run of this priority wait, the work stopped to make room for it).

    Higher and equal priorities make it wait, highest first; lower ones are stopped."""
    occupants = list(occupants)
    waits = sorted((o for o in occupants if o.priority <= priority), key=lambda o: o.priority)
    stops = [o for o in occupants if o.priority > priority]
    return waits, stops


def waiting_for(occupant: Occupant, node: str) -> str:
    return f"waiting for {occupant.label} {occupant.name} on {node}, priority {occupant.priority}"
