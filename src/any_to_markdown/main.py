"""Main orchestration module for the any-to-markdown processing engine.

Handles concurrency, input routing (files vs. URLs), structured results,
and batch directory processing.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Literal, Optional
from uuid import uuid4

from . import input_handler
from .input_handler import TranscriptUnavailableError

# --- Processing Constraints ---

# Concurrency threshold: files larger than this are processed sequentially
# (one at a time) to avoid Out-Of-Memory errors. This is especially important
# for heavy handlers like Whisper or OCR. It never rejects an input.
MAX_PARALLEL_SIZE: int = 200 * 1024 * 1024  # 200MB

# Hard cap for media downloaded by handle_yt_local: downloads larger than this
# are rejected outright. Kept separate from MAX_PARALLEL_SIZE so tuning the
# concurrency threshold never silently changes the download limit.
MAX_DOWNLOAD_SIZE: int = 200 * 1024 * 1024  # 200MB

# Number of concurrent tasks allowed for smaller files.
MAX_CONCURRENT_TASKS: int = 10

# Extensions that require Whisper transcription. These are always routed through
# a dedicated transcription semaphore so that multiple Whisper jobs never run
# concurrently, regardless of file size.
TRANSCRIPTION_EXTENSIONS: set[str] = {".mp3", ".mp4", ".wav", ".m4a"}

# Default output directory name (created in the current working directory).
DEFAULT_OUTPUT_DIR: str = "raw_data"

# Mapping of file extensions to their respective processing functions in input_handler.
HANDLERS: Dict[str, Callable[..., str]] = {
    ".txt": input_handler.handle_text,
    ".json": input_handler.handle_code,
    ".md": input_handler.handle_text,
    ".docx": input_handler.handle_document,
    ".xls": input_handler.handle_excel,
    ".xlsx": input_handler.handle_excel,
    ".csv": input_handler.handle_csv,
    ".pptx": input_handler.handle_powerpoint,
    ".pdf": input_handler.handle_pdf,
    ".png": input_handler.handle_image,
    ".jpg": input_handler.handle_image,
    ".jpeg": input_handler.handle_image,
    ".tiff": input_handler.handle_image,
    ".tif": input_handler.handle_image,
    ".bmp": input_handler.handle_image,
    ".mp3": input_handler.handle_audio,
    ".wav": input_handler.handle_audio,
    ".m4a": input_handler.handle_audio,
    ".mp4": input_handler.handle_video,
    ".ipynb": input_handler.handle_notebook,
    ".py": input_handler.handle_code,
    ".js": input_handler.handle_code,
    ".ts": input_handler.handle_code,
    ".cpp": input_handler.handle_code,
    ".c": input_handler.handle_code,
    ".h": input_handler.handle_code,
    ".hpp": input_handler.handle_code,
    ".rs": input_handler.handle_code,
    ".go": input_handler.handle_code,
    ".java": input_handler.handle_code,
    ".rb": input_handler.handle_code,
    ".php": input_handler.handle_code,
    ".sh": input_handler.handle_code,
    ".sql": input_handler.handle_code,
    ".yaml": input_handler.handle_code,
    ".yml": input_handler.handle_code,
    ".xml": input_handler.handle_code,
    ".html": input_handler.handle_html,
    ".htm": input_handler.handle_html,
    ".css": input_handler.handle_code,
}

# Single source of truth: the supported extensions are exactly the handled ones.
ALLOWED_EXTENSIONS: set[str] = set(HANDLERS)

# Allowed conversion outcomes. Using a Literal lets mypy catch status typos
# in both this package and downstream code.
ConversionStatus = Literal["success", "error", "skipped"]


@dataclass
class ConversionResult:
    """Structured outcome of a single input conversion.

    Attributes:
        input: The original input (file path or URL) as provided by the caller.
        status: One of "success", "error", or "skipped".
        content: The generated Markdown content (populated on success).
        output_path: Path to the written Markdown file (populated on success
            for functions that write to disk).
        message: Human-readable success message (populated when the caller
            explicitly specified an output_dir).
        error: Sanitized, machine-readable error description (populated on
            failure).
        suggestion: Name of a suggested alternative function for failed
            inputs (e.g. "handle_yt_local").
    """

    input: str
    status: ConversionStatus
    content: Optional[str] = None
    output_path: Optional[Path] = None
    message: Optional[str] = None
    error: Optional[str] = None
    suggestion: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True when the conversion succeeded."""
        return self.status == "success"


_URL_PATTERN = re.compile(r"https?://[^\s'\"]+")
_PATH_PATTERN = re.compile(r"(/[a-zA-Z0-9\._\-/]+)|([a-zA-Z]:\\[a-zA-Z0-9\._\-\\]+)")


def _sanitize_error(error: BaseException) -> str:
    """Sanitizes error messages to prevent leaking system-specific info.

    Replaces absolute filesystem paths with just the filename and limits the
    message length. URLs are preserved verbatim: the path portion of e.g. a
    YouTube link is useful context, not a local filesystem leak.

    Args:
        error: The exception to sanitize.

    Returns:
        A sanitized error string.
    """
    error_msg = str(error)

    # Protect URLs first so the filesystem-path regex cannot mangle them.
    urls: List[str] = []

    def stash_url(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f"\x00URL{len(urls) - 1}\x00"

    protected = _URL_PATTERN.sub(stash_url, error_msg)

    def mask_path(match: re.Match[str]) -> str:
        full_path = match.group(0)
        # Avoid masking very short strings or common symbols
        if len(full_path) > 5:
            try:
                return Path(full_path).name
            except Exception:
                return "[REDACTED_PATH]"
        return full_path

    sanitized = _PATH_PATTERN.sub(mask_path, protected)

    for i, url in enumerate(urls):
        sanitized = sanitized.replace(f"\x00URL{i}\x00", url)

    if len(sanitized) > 500:
        sanitized = sanitized[:497] + "..."

    return sanitized


def _warn_about_failures(results: List[ConversionResult]) -> None:
    """Emits warnings for failed/skipped inputs plus a batch summary.

    Args:
        results: The structured results of a batch run.
    """
    failed = [r for r in results if r.status == "error"]
    skipped = [r for r in results if r.status == "skipped"]
    if not failed and not skipped:
        return

    succeeded = len(results) - len(failed) - len(skipped)

    for result in failed:
        msg = f"Failed to convert '{result.input}': {result.error}"
        if result.suggestion:
            msg += f" Suggested alternative: {result.suggestion}()."
        warnings.warn(msg, UserWarning, stacklevel=2)

    for result in skipped:
        warnings.warn(f"Skipped '{result.input}': {result.error}", UserWarning, stacklevel=2)

    warnings.warn(
        f"Batch summary: {succeeded} succeeded, {len(failed)} failed, "
        f"{len(skipped)} skipped (out of {len(results)} inputs). "
        "See the warnings above for per-input details and suggested alternatives.",
        UserWarning,
        stacklevel=2,
    )


def _write_markdown(out_dir: Path, base_name: str, content: str) -> Path:
    """Writes content to a collision-resistant Markdown file.

    Uses exclusive-create mode ("x") so that two concurrent runs can never
    clobber each other's output (avoids the check-then-open TOCTOU race).

    Args:
        out_dir: Target directory (created if missing).
        base_name: Base filename without extension.
        content: Markdown content to write.

    Returns:
        The path of the written file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    counter = 0
    while True:
        suffix = "" if counter == 0 else f"_{counter}"
        out_path = out_dir / f"{base_name}{suffix}.md"
        try:
            with out_path.open("x", encoding="utf-8") as md_file:
                md_file.write(content)
            return out_path
        except FileExistsError:
            counter += 1


async def _process_input(
    input_val: str | Path,
    semaphore: asyncio.Semaphore,
    use_layout_engine: bool = False,
    yt_id: Optional[str] = None,
    whisper_model: Optional[str] = None,
) -> str:
    """Internal wrapper to execute handlers for files or YouTube URLs.

    Args:
        input_val: The input file path or YouTube URL (validated upstream).
        semaphore: The concurrency gate to respect.
        use_layout_engine: Whether to use advanced PDF layout analysis.
        yt_id: Pre-extracted YouTube video ID, or None for local files. The
            ID is computed exactly once by the caller and carried through.
        whisper_model: Optional Whisper model size for transcription jobs.

    Returns:
        The generated Markdown content.

    Raises:
        TranscriptUnavailableError: If a YouTube transcript is unavailable.
        Exception: Any handler error propagates to the caller, where it is
            converted into a structured error result (the batch never aborts).
    """
    async with semaphore:
        # 1. Routing: YouTube URLs (the ID was extracted once, upstream)
        if yt_id:
            metadata_header = f"\n---\nsource: YouTube\nid: {yt_id}\ntype: youtube\n---\n\n"
            content = await asyncio.to_thread(input_handler.handle_youtube, yt_id)
            return f"{metadata_header}{content}"

        # 2. Routing: Local File Handling (extension validated upstream)
        file_path = Path(input_val)
        ext = file_path.suffix.lower()
        handler = HANDLERS[ext]

        if ext == ".pdf":
            # PDF pages embed their own per-page metadata headers.
            return await asyncio.to_thread(handler, file_path, use_layout_engine)

        metadata_header = f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"
        if ext in TRANSCRIPTION_EXTENSIONS:
            content = await asyncio.to_thread(handler, file_path, whisper_model)
        else:
            content = await asyncio.to_thread(handler, file_path)
        return f"{metadata_header}{content}"


async def get_markdown(
    inputs: str | Path | Iterable[str | Path],
    use_layout_engine: bool = False,
    max_transcriptions: int = 1,
    output_dir: str | Path | None = None,
    whisper_model: Optional[str] = None,
) -> List[ConversionResult]:
    """The main conversion engine for a batch of files and YouTube URLs.

    A failed input never aborts the batch: every input yields exactly one
    structured ConversionResult, and failures additionally emit warnings.

    Args:
        inputs: A single path or a collection of file paths or YouTube URLs.
        use_layout_engine: Whether to use advanced PDF layout analysis.
        max_transcriptions: Maximum number of concurrent Whisper transcription
            jobs for audio/video files. Defaults to 1 because transcription is
            CPU/memory heavy.
        output_dir: Optional output directory for the generated Markdown
            files. Defaults to './raw_data' in the current working directory.
            When provided, successful results also carry a human-readable
            success message.
        whisper_model: Optional Whisper model size for audio/video inputs
            (e.g. 'tiny', 'small', 'medium'). Defaults to the
            ANY_TO_MARKDOWN_WHISPER_MODEL environment variable, or 'small'.

    Returns:
        One ConversionResult per input, in input order. Successful results
        contain the Markdown content and the output Path; failures produce
        status "error" (or "skipped" for unsupported formats) with a
        machine-readable error description and an optional suggestion.
    """
    if isinstance(inputs, (str, Path)):
        input_list: List[str | Path] = [inputs]
    else:
        input_list = list(inputs)

    custom_output = output_dir is not None
    out_dir = Path(output_dir) if output_dir is not None else Path.cwd() / DEFAULT_OUTPUT_DIR

    small_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    large_file_semaphore = asyncio.Semaphore(1)
    transcription_semaphore = asyncio.Semaphore(max_transcriptions)

    # Extract every YouTube ID exactly once; reused for routing, processing,
    # and output naming below.
    yt_ids: List[Optional[str]] = [input_handler.extract_youtube_id(str(item)) for item in input_list]

    results: List[Optional[ConversionResult]] = [None] * len(input_list)
    tasks: List[Optional[asyncio.Task[str]]] = [None] * len(input_list)

    for i, item in enumerate(input_list):
        input_str = str(item)
        yt_id = yt_ids[i]

        if yt_id:
            tasks[i] = asyncio.create_task(_process_input(input_str, small_task_semaphore, yt_id=yt_id))
            continue

        file_path = Path(item)
        ext = file_path.suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            results[i] = ConversionResult(
                input=input_str,
                status="skipped",
                error=f"Unsupported format: '{ext or 'no extension'}'",
            )
            continue

        if not file_path.is_file():
            results[i] = ConversionResult(
                input=input_str,
                status="error",
                error=f"File not found: {file_path.name}",
            )
            continue

        if ext in TRANSCRIPTION_EXTENSIONS:
            # Whisper jobs are CPU/memory heavy: never run N of them in
            # parallel, regardless of how small the files are.
            sem = transcription_semaphore
        else:
            file_size = file_path.stat().st_size
            sem = small_task_semaphore if file_size <= MAX_PARALLEL_SIZE else large_file_semaphore
        tasks[i] = asyncio.create_task(
            _process_input(file_path, sem, use_layout_engine, whisper_model=whisper_model)
        )

    pending = [t for t in tasks if t is not None]
    if pending:
        # return_exceptions=True guarantees that one failure never kills the
        # batch and that no in-flight task is left orphaned.
        await asyncio.gather(*pending, return_exceptions=True)

    for i, (item, task) in enumerate(zip(input_list, tasks)):
        if task is None:
            continue  # Already resolved during validation.

        input_str = str(item)
        exc = task.exception()
        if exc is not None:
            suggestion = "handle_yt_local" if isinstance(exc, TranscriptUnavailableError) else None
            results[i] = ConversionResult(
                input=input_str,
                status="error",
                error=_sanitize_error(exc),
                suggestion=suggestion,
            )
            continue

        content = task.result()
        yt_id = yt_ids[i]
        if yt_id:
            base_name = f"youtube_{yt_id}"
        else:
            file_path = Path(item)
            base_name = f"{file_path.stem}_{file_path.suffix.lower().lstrip('.')}"

        out_path = _write_markdown(out_dir, base_name, content)
        message = f"Success: '{input_str}' converted and written to '{out_path}'" if custom_output else None
        results[i] = ConversionResult(
            input=input_str,
            status="success",
            content=content,
            output_path=out_path,
            message=message,
        )

    final_results = [r for r in results if r is not None]
    _warn_about_failures(final_results)
    return final_results


async def get_markdown_directory(
    directory_path: str | Path,
    use_layout_engine: bool = False,
    max_transcriptions: int = 1,
    output_dir: str | Path | None = None,
    whisper_model: Optional[str] = None,
) -> List[ConversionResult]:
    """Crawls a directory recursively and converts supported files.

    Args:
        directory_path: Path to the source directory.
        use_layout_engine: Whether to use advanced PDF layout analysis.
        max_transcriptions: Maximum number of concurrent Whisper transcription
            jobs for audio/video files.
        output_dir: Optional output directory (defaults to './raw_data').
        whisper_model: Optional Whisper model size for audio/video inputs.

    Returns:
        A list of ConversionResult objects; empty if the directory contains
        no supported files.

    Raises:
        ValueError: If the provided path is not a valid directory.
    """
    dir_path = Path(directory_path)
    if not dir_path.is_dir():
        raise ValueError(f"The path {directory_path} is not a valid directory.")

    file_list: List[Path] = []
    for root, _, filenames in os.walk(dir_path):
        for filename in filenames:
            file_path = Path(root) / filename
            if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
                file_list.append(file_path)

    if not file_list:
        return []

    file_list.sort()
    return await get_markdown(
        file_list,
        use_layout_engine=use_layout_engine,
        max_transcriptions=max_transcriptions,
        output_dir=output_dir,
        whisper_model=whisper_model,
    )


def _download_and_transcribe(url: str, whisper_model: Optional[str]) -> str:
    """Downloads the audio-only stream for a YouTube URL and transcribes it.

    Blocking helper, executed in a worker thread by handle_yt_local_async.

    Args:
        url: The YouTube URL to download.
        whisper_model: Optional Whisper model size.

    Returns:
        The transcript text.

    Raises:
        ValueError: If the downloaded audio exceeds MAX_DOWNLOAD_SIZE.
        Exception: Any yt-dlp or transcription error propagates to the caller.
    """
    yt_dlp = input_handler.require_dependency("yt_dlp", "youtube")

    out_tmpl = str(Path(tempfile.gettempdir()) / f"yt_{uuid4()}.%(ext)s")
    ydl_opts = {
        # Audio-only download: smaller transfer, no transcode, and no FFmpeg
        # requirement, since faster-whisper accepts m4a/webm directly.
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded_file = ydl.prepare_filename(info)

    path = Path(downloaded_file)
    try:
        if path.stat().st_size > MAX_DOWNLOAD_SIZE:
            raise ValueError("file is too large to process")

        # Feed the audio file straight to Whisper; skip handle_video's
        # ffmpeg-based audio extraction entirely.
        return input_handler.handle_audio(downloaded_file, whisper_model)
    finally:
        # Guarantee cleanup of the downloaded media file.
        path.unlink(missing_ok=True)


async def handle_yt_local_async(
    urls: str | List[str],
    max_transcriptions: int = 1,
    output_dir: str | Path | None = None,
    whisper_model: Optional[str] = None,
) -> List[ConversionResult]:
    """Heavy-duty YouTube processor using Whisper transcription (async).

    Downloads the audio-only stream (no video) and feeds it directly to the
    Whisper engine. This means smaller downloads, no transcoding, and no
    FFmpeg requirement for this path (faster-whisper accepts m4a/webm
    directly). URLs are processed concurrently, but transcription jobs are
    gated by a semaphore (one at a time by default).

    Args:
        urls: A single YouTube URL or a list of URLs.
        max_transcriptions: Maximum number of concurrent transcription jobs.
        output_dir: Optional output directory. When provided, each successful
            transcription is also written to 'youtube_<video_id>.md' via the
            collision-safe writer, and the result carries output_path plus a
            human-readable message. When omitted, nothing is written to disk.
        whisper_model: Optional Whisper model size (see get_whisper_model).

    Returns:
        One ConversionResult per URL, in input order. The transcription is
        always available in each successful result's `content` field.

    Raises:
        MissingDependencyError: If the 'youtube' extra is not installed.
    """
    # Fail fast with an actionable message before doing any work.
    input_handler.require_dependency("yt_dlp", "youtube")

    url_list = [urls] if isinstance(urls, str) else list(urls)
    out_dir = Path(output_dir) if output_dir is not None else None
    semaphore = asyncio.Semaphore(max_transcriptions)

    async def process(url: str) -> ConversionResult:
        async with semaphore:
            try:
                transcription = await asyncio.to_thread(_download_and_transcribe, url, whisper_model)
            except Exception as e:
                return ConversionResult(input=url, status="error", error=_sanitize_error(e))

        result = ConversionResult(input=url, status="success", content=transcription)
        if out_dir is not None:
            video_id = input_handler.extract_youtube_id(url) or "video"
            out_path = _write_markdown(out_dir, f"youtube_{video_id}", transcription)
            result.output_path = out_path
            result.message = f"Success: '{url}' converted and written to '{out_path}'"
        return result

    results = list(await asyncio.gather(*(process(url) for url in url_list)))
    _warn_about_failures(results)
    return results


def handle_yt_local(
    urls: str | List[str],
    max_transcriptions: int = 1,
    output_dir: str | Path | None = None,
    whisper_model: Optional[str] = None,
) -> List[ConversionResult]:
    """Synchronous wrapper around handle_yt_local_async.

    Keeps the original blocking call signature working. From async code,
    await handle_yt_local_async() directly instead.

    Args:
        urls: A single YouTube URL or a list of URLs.
        max_transcriptions: Maximum number of concurrent transcription jobs.
        output_dir: Optional output directory (see handle_yt_local_async).
        whisper_model: Optional Whisper model size (see get_whisper_model).

    Returns:
        One ConversionResult per URL, in input order.

    Raises:
        RuntimeError: If called from a running event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            handle_yt_local_async(
                urls,
                max_transcriptions=max_transcriptions,
                output_dir=output_dir,
                whisper_model=whisper_model,
            )
        )
    raise RuntimeError(
        "handle_yt_local() cannot be called from a running event loop; await handle_yt_local_async() instead."
    )
