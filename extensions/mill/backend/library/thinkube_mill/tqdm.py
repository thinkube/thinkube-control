# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Progress bars of tqdm as stages of a run.

Libraries such as the Hugging Face trainers draw their own tqdm bars.
``install()`` puts a tqdm class in tqdm's place that still draws the bar and
also reports it to the run, so those bars appear in the control center with
no change to the library's code. ``mill run`` installs it before it loads a
task, when tqdm is there.

A task that wants a tqdm bar of its own imports this module's ``tqdm``.
"""

import importlib.util
from typing import Any

from . import _run


def _reporting_class() -> Any:
    import tqdm.std

    class MillTqdm(tqdm.std.tqdm):  # type: ignore[misc]
        """A tqdm bar that is also a stage of the current run."""

        _mill_stage = None

        def _mill_report(self, ended: bool = False) -> None:
            run = _run.current()
            if run is None or getattr(self, "disable", True):
                return
            if self._mill_stage is None:
                # tqdm keeps a description with ": " after it once it was set again
                name = (self.desc or "").rstrip(": ") or "progress"
                self._mill_stage = run.stage(name, self.total)
            self._mill_stage.set(self.n, self.total)
            if ended:
                self._mill_stage.end()

        def display(self, *args: Any, **kwargs: Any) -> Any:
            self._mill_report()
            return super().display(*args, **kwargs)

        def close(self) -> None:
            if self._mill_stage is not None and self._mill_stage.ended_at is None:
                self._mill_report(ended=True)
            super().close()

    return MillTqdm


def install() -> Any:
    """Put the reporting class in tqdm's place, for code that imports tqdm after this call."""
    import tqdm
    import tqdm.auto
    import tqdm.std

    reporting = _reporting_class()
    tqdm.tqdm = reporting
    tqdm.auto.tqdm = reporting
    return reporting


def install_if_present() -> bool:
    """Install when tqdm is importable. Says whether it did."""
    if importlib.util.find_spec("tqdm") is None:
        return False
    install()
    return True


def __getattr__(name: str) -> Any:
    if name == "tqdm":
        return _reporting_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
