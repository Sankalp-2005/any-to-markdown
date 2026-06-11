"""Main orchestration module for the any-to-markdown processing engine.

Handles concurrency, input routing (files vs. URLs), and batch directory processing.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Union
from uuid import uuid4

import yt_dlp

from . import input_handler

# --- Processing Constraints ---

# Files larger than this will be processed sequentially to avoid Out-Of-Memory (OOM) errors.
# This is especially important for heavy handlers like Whisper or OCR.
MAX_PARALLEL_SIZE: int = 200 * 1024 * 1024  # 200MB

# Number of concurrent tasks allowed for smaller files.
MAX_CONCURRENT_TASKS: int = 10

# Globally recognized file extensions for the processing pipeline.
ALLOWED_EXTENSIONS: set[str] = {
    ".txt",
    ".json",
    ".md",
    ".docx",
    ".xls",
    ".xlsx",
    ".pptx",
    ".pdf",
    ".png",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".ipynb",
    ".py",
    ".js",
    ".ts",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".sh",
    ".sql",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
}

# Mapping of file extensions to their respective processing functions in input_handler.
HANDLERS: Dict[str, Callable[[Union[str, Path], ...], str]] = {
    ".txt": input_handler.handle_text,
    ".json": input_handler.handle_code,
    ".md": input_handler.handle_text,
    ".docx": input_handler.handle_document,
    ".xls": input_handler.handle_excel,
    ".xlsx": input_handler.handle_excel,
    ".pptx": input_handler.handle_powerpoint,
    ".pdf": input_handler.handle_pdf,
    ".png": input_handler.handle_image,
    ".jpg": input_handler.handle_image,
    ".jpeg": input_handler.handle_image,
    ".mp3": input_handler.handle_audio,
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
    ".html": input_handler.handle_code,
    ".css": input_handler.handle_code,
}


def _sanitize_error(error: Exception) -> str:
    """Sanitizes error messages to prevent leaking system-specific info.

    Replaces absolute paths with just the filename and limits message length.

    Args:
        error: The exception to sanitize.

    Returns:
        A sanitized error string.
    """
    error_msg = str(error)

    # Regex to identify potential absolute paths (Unix and Windows styles)
    path_regex = r"(/[a-zA-Z0-9\._\-/]+)|([a-zA-Z]:\\[a-zA-Z0-9\._\-\\]+)"

    def mask_path(match: re.Match[str]) -> str:
        full_path = match.group(0)
        # Avoid masking very short strings or common symbols
        if len(full_path) > 5:
            try:
                return Path(full_path).name
            except Exception:
                return "[REDACTED_PATH]"
        return full_path

    sanitized = re.sub(path_regex, mask_path, error_msg)

    if len(sanitized) > 500:
        sanitized = sanitized[:497] + "..."

    return sanitized


async def _process_input(input_val: str | Path, semaphore: asyncio.Semaphore, use_layout_engine: bool = False) -> str:
    """Internal wrapper to execute handlers for files or YouTube URLs.

    Args:
        input_val: The input file path or YouTube URL.
        semaphore: The concurrency gate to respect.
        use_layout_engine: Whether to use advanced PDF layout analysis.

    Returns:
        The generated Markdown content.

    Raises:
        RuntimeError: If a YouTube transcript is unavailable.
    """
    async with semaphore:
        input_str = str(input_val)

        # 1. Routing: Check if input is a YouTube URL
        yt_id = input_handler.extract_youtube_id(input_str)
        if yt_id:
            metadata_header = f"\n---\nsource: YouTube\nid: {yt_id}\ntype: youtube\n---\n\n"
            try:
                content = await asyncio.to_thread(input_handler.handle_youtube, yt_id)
                return f"{metadata_header}{content}"
            except Exception as e:
                raise RuntimeError(
                    f"No transcript available for YouTube video {yt_id}. Original error: {str(e)}. Please use 'handle_yt_local' for this video."
                ) from e

        # 2. Routing: Local File Handling
        file_path = Path(input_val)
        ext = file_path.suffix.lower()
        handler = HANDLERS.get(ext)

        if ext == ".pdf":
            metadata_header = ""
        else:
            metadata_header = f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"

        if not handler:
            unsupported_header = f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"
            return f"{unsupported_header}### [Warning: Unsupported format {ext}]\n\n"

        try:
            if ext == ".pdf":
                content = await asyncio.to_thread(handler, file_path, use_layout_engine)
            else:
                content = await asyncio.to_thread(handler, file_path)
            return f"{metadata_header}{content}"
        except Exception as e:
            error_header = metadata_header or f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"
            sanitized_error = _sanitize_error(e)
            return f"{error_header}### [Error processing file: {sanitized_error}]\n\n"


async def get_markdown(inputs: str | Path | Iterable[str | Path], use_layout_engine: bool = False) -> List[str]:
    """The main conversion engine for a batch of files and YouTube URLs.

    Args:
        inputs: A single path or a collection of file paths or YouTube URLs.
        use_layout_engine: Whether to use advanced PDF layout analysis.

    Returns:
        A list of absolute paths to the generated Markdown files.
    """
    if isinstance(inputs, (str, Path)):
        input_list = [inputs]
    else:
        input_list = list(inputs)

    small_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    large_file_semaphore = asyncio.Semaphore(1)

    raw_data_dir = Path.cwd() / "raw_data"
    raw_data_dir.mkdir(parents=True, exist_ok=True)

    pending_tasks: List[asyncio.Task[str] | asyncio.Future[str]] = []
    for item in input_list:
        input_str = str(item)
        yt_id = input_handler.extract_youtube_id(input_str)

        if yt_id:
            pending_tasks.append(asyncio.create_task(_process_input(input_str, small_task_semaphore)))
            continue

        file_path = Path(item)
        if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            error_msg = f"\n\n---\n### [Warning: Input not supported: {file_path.name}]\n---\n\n"
            f: asyncio.Future[str] = asyncio.Future()
            f.set_result(error_msg)
            pending_tasks.append(f)
            continue

        file_size = file_path.stat().st_size
        sem = small_task_semaphore if file_size <= MAX_PARALLEL_SIZE else large_file_semaphore
        pending_tasks.append(asyncio.create_task(_process_input(file_path, sem, use_layout_engine)))

    output_paths: List[str] = []
    for item, task in zip(input_list, pending_tasks):
        result = await task
        input_str = str(item)
        yt_id = input_handler.extract_youtube_id(input_str)

        if yt_id:
            base_name = f"youtube_{yt_id}"
        else:
            file_path = Path(item)
            ext_clean = file_path.suffix.lower().lstrip(".")
            base_name = f"{file_path.stem}_{ext_clean}"

        out_name = f"{base_name}.md"
        out_path = raw_data_dir / out_name

        counter = 1
        while out_path.exists():
            out_name = f"{base_name}_{counter}.md"
            out_path = raw_data_dir / out_name
            counter += 1

        with out_path.open("w", encoding="utf-8") as md_file:
            md_file.write(result)

        output_paths.append(str(out_path))

    return output_paths


async def get_markdown_directory(directory_path: str | Path, use_layout_engine: bool = False) -> Optional[List[str]]:
    """Crawls a directory recursively and converts supported files.

    Args:
        directory_path: Path to the source directory.
        use_layout_engine: Whether to use advanced PDF layout analysis.

    Returns:
        List of output file paths, or None if no supported files found.

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
        return None

    file_list.sort()
    return await get_markdown(file_list, use_layout_engine=use_layout_engine)


def handle_yt_local(urls: str | List[str]) -> List[str]:
    """Heavy-duty YouTube processor using Whisper transcription.

    Args:
        urls: A single YouTube URL or a list of URLs.

    Returns:
        A list of generated Markdown transcription strings.
    """
    url_list = [urls] if isinstance(urls, str) else urls
    transcriptions: List[str] = []

    for url in url_list:
        temp_dir = tempfile.gettempdir()
        out_tmpl = str(Path(temp_dir) / f"yt_{uuid4()}.%(ext)s")

        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": out_tmpl,
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)

            path = Path(downloaded_file)
            if path.stat().st_size > 200 * 1024 * 1024:
                path.unlink(missing_ok=True)
                raise ValueError("file is too large to process")

            transcription = input_handler.handle_video(downloaded_file)
            transcriptions.append(transcription)
            path.unlink(missing_ok=True)

        except Exception as e:
            sanitized_error = _sanitize_error(e)
            transcriptions.append(f"\n\n### [Error processing YouTube video locally: {sanitized_error}]\n\n")

    return transcriptions
