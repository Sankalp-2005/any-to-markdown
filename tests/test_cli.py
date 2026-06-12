"""Tests for the any-to-markdown command-line interface."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from any_to_markdown import __version__
from any_to_markdown.cli import app

runner = CliRunner()


def test_cli_converts_file_to_custom_output_dir(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("hello from the cli", encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(app, [str(source), "-o", str(out_dir)])

    assert result.exit_code == 0
    assert "1/1 inputs converted." in result.stdout
    written = list(out_dir.glob("*.md"))
    assert len(written) == 1
    assert "hello from the cli" in written[0].read_text(encoding="utf-8")


def test_cli_exits_nonzero_when_an_input_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, [str(tmp_path / "missing.txt"), "-o", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "0/1 inputs converted." in result.stdout


def test_cli_converts_directories(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "b.txt").write_text("beta", encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(app, [str(docs), "-o", str(out_dir)])

    assert result.exit_code == 0
    assert "2/2 inputs converted." in result.stdout
    assert len(list(out_dir.glob("*.md"))) == 2


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
