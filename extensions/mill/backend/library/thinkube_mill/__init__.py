# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The task standard of Thinkube Mill.

A task is a function that declares its inputs, its outputs and its settings
with ``@task``. ``mill run`` reads the inputs, calls the function, writes
the outputs, and keeps a record of the run that the control center shows:
progress per stage, samples of processed items, counts and failures.

Inside a task, ``track`` reports progress, ``show`` sends a sample,
``count`` and ``measure`` keep running totals, and ``fail`` records an item
that went wrong. Called outside a run, as plain Python, they do nothing, so
a task's function can be imported and called anywhere.
"""

from ._run import count, fail, measure, show, track
from ._task import Task, TaskError, task

__all__ = ["task", "track", "show", "count", "measure", "fail", "Task", "TaskError"]
__version__ = "0.1.0"
