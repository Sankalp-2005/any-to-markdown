"""Progress reporting for batch conversions.

A tiny indirection layer between the orchestration code in ``main.py`` and the
actual rendering. ``main.py`` only talks to the :class:`ProgressReporter`
protocol, so the library stays free of forced terminal output: when progress is
disabled (non-TTY, ``show_progress=False``, or the ``ANY_TO_MARKDOWN_NO_PROGRESS``
env var), the no-op :class:`_NullProgressReporter` is used and nothing is
written anywhere. When enabled, :class:`_RichProgressReporter` renders a live
``rich`` progress bar to stderr.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID

# Env var that fully disables the progress bar regardless of TTY status, so
# library callers and CI logs can opt out globally without code changes.
NO_PROGRESS_ENV_VAR: str = "ANY_TO_MARKDOWN_NO_PROGRESS"


@runtime_checkable
class ProgressReporter(Protocol):
    """Minimal interface the batch engine drives during a run.

    ``start`` is called with the total number of inputs that will be awaited;
    ``advance`` is called once per completed input (success or failure); and
    ``stop`` finalises the bar. Implementations must tolerate being driven to
    completion even when ``total`` is zero.
    """

    def start(self, total: int) -> None: ...

    def advance(self) -> None: ...

    def stop(self) -> None: ...


class _NullProgressReporter:
    """No-op reporter used when progress is disabled.

    Doing nothing here (rather than guarding every call site in ``main.py``)
    keeps the orchestration code path identical whether or not a bar is shown.
    """

    def start(self, total: int) -> None:  # noqa: ARG002 - signature parity
        return None

    def advance(self) -> None:
        return None

    def stop(self) -> None:
        return None


class _RichProgressReporter:
    """Live progress bar backed by ``rich.progress``.

    Renders to ``sys.stderr`` so it never interferes with stdout-based output
    (including the CLI's per-input summary lines and stdout test assertions).
    The underlying ``Progress`` is created lazily on ``start`` so disabled runs
    never import ``rich`` at all.
    """

    def __init__(self, description: str = "Converting") -> None:
        self._description = description
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def start(self, total: int) -> None:
        from rich.progress import (  # Local import: skipped entirely when disabled.
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=None,  # Defaults to stderr in rich.
            transient=False,
        )
        self._task_id = self._progress.add_task(self._description, total=total)
        self._progress.start()

    def advance(self) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.advance(self._task_id)

    def stop(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task_id = None


def _is_progress_disabled_by_env() -> bool:
    """True when the ``ANY_TO_MARKDOWN_NO_PROGRESS`` env var is set to a truthy value."""
    raw = os.environ.get(NO_PROGRESS_ENV_VAR)
    if raw is None:
        return False
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _stderr_is_tty() -> bool:
    """Whether stderr is an interactive terminal we can render a live bar to."""
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        # ValueError happens on Windows when the fd is detached/redirected.
        return False


def default_progress_reporter(show: bool, description: str = "Converting") -> ProgressReporter:
    """Pick the right reporter for the current process.

    The Rich bar is used only when *all* of these hold:
      * the caller asked for progress (``show=True``),
      * the ``ANY_TO_MARKDOWN_NO_PROGRESS`` env var is not set,
      * stderr is a TTY (so piped/CI/non-interactive runs stay silent).

    Otherwise the no-op reporter is returned, preserving the library's
    historical silent-by-default-in-scripts behavior.
    """
    if not show or _is_progress_disabled_by_env() or not _stderr_is_tty():
        return _NullProgressReporter()
    return _RichProgressReporter(description=description)
