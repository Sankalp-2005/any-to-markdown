"""Tests for progress-reporting behavior in progress.py and main.py.

These tests stay free of heavy backends (no PDF/OCR/Whisper/YouTube): they
convert tiny ``.txt`` files or monkeypatch the YouTube handler, and they assert
on the *choice* of reporter (Rich vs Null) rather than on rendered output. That
keeps them fast and deterministic in CI's non-TTY environment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import List

import pytest

from any_to_markdown import main
from any_to_markdown import progress


class _CountingReporter:
    """Test double that records how the batch engine drove the reporter.

    Implements the ProgressReporter protocol structurally. It tracks start/stop
    pairing, the declared total, and how many times advance() fired, so tests
    can assert on the lifecycle without touching rich.
    """

    def __init__(self) -> None:
        self.started: bool = False
        self.stopped: bool = False
        self.total: int = -1
        self.advances: int = 0

    def start(self, total: int) -> None:
        self.started = True
        self.total = total

    def advance(self) -> None:
        self.advances += 1

    def stop(self) -> None:
        self.stopped = True


def _force_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stderr is a TTY so the Rich path is reachable under pytest."""
    monkeypatch.setattr(progress.sys.stderr, "isatty", lambda: True)


def _force_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stderr is NOT a TTY (the realistic pytest/CI state)."""
    monkeypatch.setattr(progress.sys.stderr, "isatty", lambda: False)


# --- reporter selection ------------------------------------------------------


def test_reporter_is_null_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_not_tty(monkeypatch)
    monkeypatch.delenv(progress.NO_PROGRESS_ENV_VAR, raising=False)

    reporter = progress.default_progress_reporter(show=True)

    assert isinstance(reporter, progress._NullProgressReporter)


def test_reporter_is_rich_on_tty_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_tty(monkeypatch)
    monkeypatch.delenv(progress.NO_PROGRESS_ENV_VAR, raising=False)

    reporter = progress.default_progress_reporter(show=True)

    assert isinstance(reporter, progress._RichProgressReporter)


def test_reporter_is_null_when_show_progress_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even on a TTY, an explicit show=False must win.
    _force_tty(monkeypatch)
    monkeypatch.delenv(progress.NO_PROGRESS_ENV_VAR, raising=False)

    reporter = progress.default_progress_reporter(show=False)

    assert isinstance(reporter, progress._NullProgressReporter)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
def test_no_progress_env_var_disables_bar(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _force_tty(monkeypatch)
    monkeypatch.setenv(progress.NO_PROGRESS_ENV_VAR, value)

    reporter = progress.default_progress_reporter(show=True)

    assert isinstance(reporter, progress._NullProgressReporter)


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_no_progress_env_var_falsy_values_keep_bar_enabled(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _force_tty(monkeypatch)
    monkeypatch.setenv(progress.NO_PROGRESS_ENV_VAR, value)

    reporter = progress.default_progress_reporter(show=True)

    assert isinstance(reporter, progress._RichProgressReporter)


# --- reporter lifecycle via the batch engine --------------------------------


def _inject_counting_reporter(monkeypatch: pytest.MonkeyPatch) -> _CountingReporter:
    """Replace the factory so get_markdown drives our counting reporter."""
    counter = _CountingReporter()
    monkeypatch.setattr(main, "default_progress_reporter", lambda *a, **k: counter)
    return counter


def test_get_markdown_drives_reporter_once_per_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    counter = _inject_counting_reporter(monkeypatch)

    results = asyncio.run(
        main.get_markdown([str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), str(tmp_path / "c.txt")])
    )

    assert len(results) == 3
    assert all(r.ok for r in results)
    # Lifecycle: start(total=3), three advances, stop.
    assert counter.started and counter.stopped
    assert counter.total == 3
    assert counter.advances == 3


def test_get_markdown_advances_on_runtime_failure_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler that fails at runtime still advances the bar to its total.

    Inputs that fail *validation* (missing file, unsupported extension) are
    recorded synchronously before any task is created, so they never reach the
    gather and are not part of the bar's total. But an input that passes
    validation and then fails *inside its handler* must still advance so the
    bar completes. We simulate the latter by making a real handler raise.

    The HANDLERS dict is patched directly (via setitem) because it captures
    function references at module load: monkeypatching the module attribute
    would not change the dict entry the engine actually dispatches through.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
    boom = tmp_path / "boom.txt"
    boom.write_text("will explode", encoding="utf-8")

    original_handle_text = main.HANDLERS[".txt"]

    def failing_handle(path: Path) -> str:
        if Path(path).name == "boom.txt":
            raise RuntimeError("handler exploded")
        return original_handle_text(path)

    monkeypatch.setitem(main.HANDLERS, ".txt", failing_handle)
    counter = _inject_counting_reporter(monkeypatch)

    with pytest.warns(UserWarning, match="Batch summary"):
        results = asyncio.run(main.get_markdown([str(tmp_path / "good.txt"), str(boom)]))

    assert [r.status for r in results] == ["success", "error"]
    # Both inputs passed validation and entered the gather, so both advance()
    # calls fired: the bar reached its total despite the runtime failure.
    assert counter.total == 2
    assert counter.advances == 2
    assert counter.stopped


def test_get_markdown_skips_validation_failures_outside_the_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation failures are excluded from the progress bar's total.

    A missing file is turned into an error result synchronously (before tasks
    are built), so it is never awaited and never counted by the reporter. Only
    the one valid input drives the bar.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
    counter = _inject_counting_reporter(monkeypatch)

    with pytest.warns(UserWarning, match="Batch summary"):
        results = asyncio.run(main.get_markdown([str(tmp_path / "good.txt"), str(tmp_path / "missing.txt")]))

    assert [r.status for r in results] == ["success", "error"]
    # Only the existing file reached the gather.
    assert counter.total == 1
    assert counter.advances == 1
    assert counter.stopped


def test_get_markdown_directory_forwards_progress_reporter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a.txt", "b.txt"):
        (docs / name).write_text(name, encoding="utf-8")
    counter = _inject_counting_reporter(monkeypatch)

    results = asyncio.run(main.get_markdown_directory(docs))

    assert len(results) == 2
    assert counter.started and counter.stopped
    assert counter.total == 2
    assert counter.advances == 2


def test_get_markdown_directory_empty_dir_does_not_start_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _inject_counting_reporter(monkeypatch)

    results = asyncio.run(main.get_markdown_directory(tmp_path))

    assert results == []
    # No files => no gather => reporter never started.
    assert not counter.started
    assert not counter.stopped
    assert counter.advances == 0


# --- YouTube path drives the same reporter ----------------------------------


def test_handle_yt_local_async_drives_reporter_per_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bypass the real yt-dlp/whisper backend: pretend the dependency is present
    # and that each download+transcribe returns canned text.
    monkeypatch.setattr(main.input_handler, "require_dependency", lambda name, extra: True)

    urls: List[str] = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/aaaaaaaaaaa",
    ]
    monkeypatch.setattr(
        main,
        "_download_and_transcribe",
        lambda url, whisper_model: f"transcript for {url}",
    )
    counter = _inject_counting_reporter(monkeypatch)

    results = asyncio.run(main.handle_yt_local_async(urls))

    assert len(results) == 2
    assert all(r.ok for r in results)
    assert counter.started and counter.stopped
    assert counter.total == 2
    assert counter.advances == 2


# --- NullProgressReporter correctness ---------------------------------------


def test_null_reporter_is_silent_and_safe() -> None:
    reporter = progress._NullProgressReporter()
    # All methods must be callable and return None for any input.
    assert reporter.start(0) is None
    assert reporter.start(1_000_000) is None
    assert reporter.advance() is None
    assert reporter.stop() is None


# --- Real Rich path smoke test (only meaningful on a TTY) -------------------


def test_rich_reporter_start_stop_is_idempotent() -> None:
    """A Rich reporter must tolerate stop() even if start() was never called,
    and tolerate repeated stop() calls without leaking an active live display."""
    reporter = progress._RichProgressReporter()
    # stop() before start() must not raise.
    reporter.stop()
    reporter.start(2)
    reporter.advance()
    reporter.advance()
    reporter.stop()
    # A second stop() after start()+stop() must also be safe.
    reporter.stop()
    # Importing rich here is fine: it is a declared core dependency.
    import rich.progress  # noqa: F401


# --- Ensure stderr is restored (no leaked monkeypatch on real stderr) -------


def test_isatty_check_does_not_raise_on_detached_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the Windows "detached fd" case where isatty() raises ValueError.
    def raise_value_error() -> bool:
        raise ValueError("detached")

    monkeypatch.setattr(sys.stderr, "isatty", raise_value_error)

    assert progress._stderr_is_tty() is False
