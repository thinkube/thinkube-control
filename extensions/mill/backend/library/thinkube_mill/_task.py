# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The declaration of a task: its inputs, its outputs and its settings."""

import inspect
from typing import Any, Callable, Dict, Optional

# What an input or an output can be:
#   jsonl  a file with one JSON value per line; read item by item, written item by item
#   json   a file with one JSON value
#   text   a file read or written as one string
#   path   a file or a folder the task reads or writes itself
TYPES = ("jsonl", "json", "text", "path")

# The parameters a task's function may have besides its inputs
SETTINGS_PARAMETER = "settings"
OUTPUTS_PARAMETER = "out"


class TaskError(Exception):
    """A task is declared or called in a way the standard does not allow."""


class Task:
    """A function with its declared inputs, outputs and settings."""

    def __init__(
        self,
        fn: Callable[..., Any],
        name: str,
        inputs: Dict[str, str],
        outputs: Dict[str, str],
        settings: Dict[str, Any],
    ) -> None:
        self.fn = fn
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.settings = settings
        self.description = inspect.getdoc(fn) or ""
        self.__doc__ = fn.__doc__
        self.__name__ = getattr(fn, "__name__", name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the function as plain Python, with no run and no record."""
        return self.fn(*args, **kwargs)

    def describe(self) -> Dict[str, Any]:
        """The declaration, as a workflow or the control center reads it."""
        return {
            "name": self.name,
            "description": self.description,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "settings": dict(self.settings),
        }


def _check_ports(kind: str, name: str, ports: Dict[str, str]) -> None:
    for port, port_type in ports.items():
        if not isinstance(port, str) or not port.isidentifier():
            raise TaskError(f"task '{name}': the {kind} name {port!r} is not a Python name")
        if port in (SETTINGS_PARAMETER, OUTPUTS_PARAMETER):
            raise TaskError(f"task '{name}': '{port}' is reserved and cannot name an {kind}")
        if port_type not in TYPES:
            raise TaskError(
                f"task '{name}': the {kind} '{port}' has type {port_type!r}; the types are {', '.join(TYPES)}"
            )


def task(
    *,
    inputs: Optional[Dict[str, str]] = None,
    outputs: Optional[Dict[str, str]] = None,
    settings: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Task]:
    """Declare a function as a task.

    ``inputs`` and ``outputs`` map a name to a type: jsonl, json, text or
    path. ``settings`` maps a name to its default value; a run may replace a
    value with another of the same kind.

    The function has one parameter for each input, with the input's name.
    It may also have ``settings`` (the settings of the run, a dict) and
    ``out`` (the outputs, by name). A function that yields writes each
    yielded item to the task's one jsonl output.
    """
    inputs = dict(inputs or {})
    outputs = dict(outputs or {})
    settings = dict(settings or {})

    def declare(fn: Callable[..., Any]) -> Task:
        task_name = name or fn.__name__
        _check_ports("input", task_name, inputs)
        _check_ports("output", task_name, outputs)
        both = sorted(set(inputs) & set(outputs))
        if both:
            raise TaskError(f"task '{task_name}': {', '.join(both)} is both an input and an output")
        for key in settings:
            if not isinstance(key, str) or not key.isidentifier():
                raise TaskError(f"task '{task_name}': the setting name {key!r} is not a Python name")

        parameters = inspect.signature(fn).parameters
        allowed = set(inputs) | {SETTINGS_PARAMETER, OUTPUTS_PARAMETER}
        unknown = [p for p in parameters if p not in allowed]
        if unknown:
            raise TaskError(
                f"task '{task_name}': the function has the parameter(s) {', '.join(unknown)}, which are not "
                f"declared inputs. A parameter is an input's name, '{SETTINGS_PARAMETER}' or '{OUTPUTS_PARAMETER}'."
            )
        missing = [i for i in inputs if i not in parameters]
        if missing:
            raise TaskError(
                f"task '{task_name}': the input(s) {', '.join(missing)} are declared, "
                "but the function has no parameter with that name"
            )
        if inspect.isgeneratorfunction(fn):
            jsonl_outputs = [o for o, t in outputs.items() if t == "jsonl"]
            if len(jsonl_outputs) != 1:
                raise TaskError(
                    f"task '{task_name}': a function that yields writes to the task's one jsonl output, "
                    f"and this task declares {len(jsonl_outputs)}. Declare exactly one, or write through 'out'."
                )
        return Task(fn, task_name, inputs, outputs, settings)

    return declare
