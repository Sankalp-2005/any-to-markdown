"""Command-line interface for any-to-markdown.

Installed as the `any-to-markdown` console script. Accepts a mix of file
paths, directories, and YouTube URLs, converts everything, prints a
per-input summary, and exits non-zero if any input errored.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import typer

from . import __version__
from .main import MAX_CONCURRENT_PDFS, ConversionResult, get_markdown, get_markdown_directory

app = typer.Typer(add_completion=False, help="Convert files, directories, and YouTube links to Markdown.")


def _version_callback(value: bool) -> None:
    """Prints the package version and exits."""
    if value:
        typer.echo(f"any-to-markdown {__version__}")
        raise typer.Exit()


async def _convert(
    inputs: List[str],
    output_dir: Optional[Path],
    layout: bool,
    pdf_tables: bool,
    max_pdf_tasks: int,
    max_transcriptions: int,
    whisper_model: Optional[str],
) -> List[ConversionResult]:
    """Routes directories through the directory crawler and the rest through get_markdown."""
    files_and_urls: List[str] = []
    results: List[ConversionResult] = []

    for item in inputs:
        if Path(item).is_dir():
            results.extend(
                await get_markdown_directory(
                    item,
                    use_layout_engine=layout,
                    extract_pdf_tables=pdf_tables,
                    max_pdf_tasks=max_pdf_tasks,
                    max_transcriptions=max_transcriptions,
                    output_dir=output_dir,
                    whisper_model=whisper_model,
                    show_progress=True,
                )
            )
        else:
            files_and_urls.append(item)

    if files_and_urls:
        results.extend(
            await get_markdown(
                files_and_urls,
                use_layout_engine=layout,
                extract_pdf_tables=pdf_tables,
                max_pdf_tasks=max_pdf_tasks,
                max_transcriptions=max_transcriptions,
                output_dir=output_dir,
                whisper_model=whisper_model,
                show_progress=True,
            )
        )
    return results


@app.command()
def convert(
    inputs: List[str] = typer.Argument(..., help="File paths, directories, or YouTube URLs to convert."),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", "-o", help="Directory for the generated Markdown files (defaults to ./raw_data)."
    ),
    layout: bool = typer.Option(False, "--layout", help="Use the pymupdf4llm layout engine for PDFs."),
    pdf_tables: bool = typer.Option(
        True,
        "--pdf-tables/--no-pdf-tables",
        help="Run built-in PyMuPDF table detection (default on, so spreadsheet PDFs keep their tables).",
    ),
    max_pdf_tasks: int = typer.Option(
        MAX_CONCURRENT_PDFS,
        "--max-pdf-tasks",
        min=1,
        help="Maximum concurrent PDF conversions (default: half the CPU cores).",
    ),
    max_transcriptions: int = typer.Option(
        1, "--max-transcriptions", min=1, help="Maximum concurrent Whisper transcription jobs."
    ),
    whisper_model: Optional[str] = typer.Option(
        None,
        "--whisper-model",
        help="Whisper model size (e.g. tiny, small, medium). Defaults to ANY_TO_MARKDOWN_WHISPER_MODEL or 'small'.",
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Convert INPUTS to Markdown and print a per-input summary."""
    results = asyncio.run(
        _convert(inputs, output_dir, layout, pdf_tables, max_pdf_tasks, max_transcriptions, whisper_model)
    )

    errored = 0
    for result in results:
        if result.ok:
            target = str(result.output_path) if result.output_path is not None else "(in-memory)"
            typer.secho(f"success  {result.input} -> {target}", fg=typer.colors.GREEN)
        else:
            if result.status == "error":
                errored += 1
            color = typer.colors.RED if result.status == "error" else typer.colors.YELLOW
            typer.secho(f"{result.status:8} {result.input}: {result.error}", fg=color)
            if result.suggestion:
                typer.secho(f"         hint: try {result.suggestion}()", fg=color)

    succeeded = sum(r.ok for r in results)
    typer.echo(f"{succeeded}/{len(results)} inputs converted.")
    if errored:
        raise typer.Exit(code=1)
