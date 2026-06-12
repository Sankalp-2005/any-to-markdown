"""Tests for the orchestration layer in main.py."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from any_to_markdown import input_handler, main


def test_get_markdown_missing_file_returns_error_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.warns(UserWarning, match="File not found"):
        results = asyncio.run(main.get_markdown(str(tmp_path / "missing.pdf")))

    assert len(results) == 1
    result = results[0]
    assert result.status == "error"
    assert not result.ok
    assert result.error is not None and "missing.pdf" in result.error
    assert result.output_path is None
    assert result.content is None


def test_get_markdown_youtube_with_mocked_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_handle_youtube(video_id: str) -> str:
        assert video_id == "dQw4w9WgXcQ"
        return "\n\nmocked transcript text\n\n"

    monkeypatch.setattr(input_handler, "handle_youtube", fake_handle_youtube)

    results = asyncio.run(main.get_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))

    assert len(results) == 1
    result = results[0]
    assert result.ok
    assert result.content is not None and "mocked transcript text" in result.content
    assert result.output_path is not None and result.output_path.exists()
    assert "id: dQw4w9WgXcQ" in result.output_path.read_text(encoding="utf-8")
    # Default output dir: a Path object is returned, no message is set.
    assert result.message is None


def test_get_markdown_custom_output_dir_returns_success_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "note.txt"
    source.write_text("hello world", encoding="utf-8")
    out_dir = tmp_path / "converted"

    results = asyncio.run(main.get_markdown(str(source), output_dir=out_dir))

    result = results[0]
    assert result.ok
    assert result.message is not None and "Success" in result.message
    assert result.output_path is not None and result.output_path.parent == out_dir
    # The default directory must not be created when output_dir is given.
    assert not (tmp_path / "raw_data").exists()


def test_one_failure_never_kills_the_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    good = tmp_path / "good.txt"
    good.write_text("fine content", encoding="utf-8")

    with pytest.warns(UserWarning, match="Batch summary"):
        results = asyncio.run(main.get_markdown([str(good), str(tmp_path / "missing.txt")]))

    assert [r.status for r in results] == ["success", "error"]
    assert results[0].output_path is not None and results[0].output_path.exists()


def test_unsupported_extension_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    weird = tmp_path / "data.xyz"
    weird.write_text("?", encoding="utf-8")

    with pytest.warns(UserWarning, match="Unsupported"):
        results = asyncio.run(main.get_markdown(str(weird)))

    assert results[0].status == "skipped"
    # Nothing succeeded, so no output directory should be created.
    assert not (tmp_path / "raw_data").exists()


def test_transcription_extensions_are_gated() -> None:
    assert main.TRANSCRIPTION_EXTENSIONS == {".mp3", ".mp4", ".wav", ".m4a"}


def test_sanitize_error_preserves_urls_and_masks_paths() -> None:
    exc = ValueError("Failed for https://www.youtube.com/watch?v=dQw4w9WgXcQ while reading /home/user/secret/file.txt")

    sanitized = main._sanitize_error(exc)

    # The URL must survive intact (its path portion is not a local path leak).
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in sanitized
    # The absolute filesystem path must be masked down to the filename.
    assert "/home/user/secret" not in sanitized
    assert "file.txt" in sanitized


def test_get_markdown_directory_empty_returns_empty_list(tmp_path: Path) -> None:
    results = asyncio.run(main.get_markdown_directory(tmp_path))
    assert results == []


def test_download_cap_is_distinct_from_concurrency_threshold() -> None:
    # Both exist independently so tuning one never silently changes the other.
    assert main.MAX_DOWNLOAD_SIZE == 200 * 1024 * 1024
    assert main.MAX_PARALLEL_SIZE == 200 * 1024 * 1024
