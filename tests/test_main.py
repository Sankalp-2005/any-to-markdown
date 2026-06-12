"""Tests for the orchestration layer in main.py."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from any_to_markdown import input_handler, main


def test_get_markdown_missing_file_writes_error_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    outputs = asyncio.run(main.get_markdown(str(tmp_path / "missing.pdf")))

    assert len(outputs) == 1
    content = Path(outputs[0]).read_text(encoding="utf-8")
    assert "File not found" in content
    assert "missing.pdf" in content


def test_get_markdown_youtube_with_mocked_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_handle_youtube(video_id: str) -> str:
        assert video_id == "dQw4w9WgXcQ"
        return "\n\nmocked transcript text\n\n"

    monkeypatch.setattr(input_handler, "handle_youtube", fake_handle_youtube)

    outputs = asyncio.run(main.get_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))

    assert len(outputs) == 1
    content = Path(outputs[0]).read_text(encoding="utf-8")
    assert "mocked transcript text" in content
    assert "id: dQw4w9WgXcQ" in content


def test_transcription_extensions_are_gated() -> None:
    assert main.TRANSCRIPTION_EXTENSIONS == {".mp3", ".mp4", ".wav", ".m4a"}
