"""
Main orchestration module for the any-to-markdown processing engine.

Handles concurrency, input routing (files vs. URLs), and batch directory processing.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable, List
from uuid import uuid4

import yt_dlp

from . import input_handler

# --- Processing Constraints ---

# Files larger than this will be processed sequentially to avoid Out-Of-Memory (OOM) errors.
# This is especially important for heavy handlers like Whisper or OCR.
MAX_PARALLEL_SIZE = 200 * 1024 * 1024  # 200MB

# Number of concurrent tasks allowed for smaller files.
MAX_CONCURRENT_TASKS = 10

# Globally recognized file extensions for the processing pipeline.
allowed_extensions = {
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
}

# Mapping of file extensions to their respective processing functions in input_handler.
HANDLERS: dict[str, Callable[[str | Path], str]] = {
    ".txt": input_handler.handle_text,
    ".json": input_handler.handle_text,
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
}


async def _process_input(input_val: str | Path, semaphore: asyncio.Semaphore) -> str:
    """Internal wrapper to execute handlers for files or YouTube URLs.

    Features:
    - Concurrency control via semaphores.
    - Automatic routing to YouTube transcript API vs. local file handlers.
    - Thread-pool offloading for CPU-bound tasks via asyncio.to_thread.

    Args:
        input_val (str | Path): The input file path or YouTube URL.
        semaphore (asyncio.Semaphore): The concurrency gate to respect.

    Returns:
        str: The generated Markdown content.

    Raises:
        RuntimeError: If a YouTube transcript is unavailable and requires local processing.
    """
    async with semaphore:
        input_str = str(input_val)

        # 1. Routing: Check if input is a YouTube URL
        yt_id = input_handler.extract_youtube_id(input_str)
        if yt_id:
            metadata_header = f"\n---\nsource: YouTube\nid: {yt_id}\ntype: youtube\n---\n\n"
            try:
                # YouTube fetching is mostly I/O bound but we use a thread to keep loop free
                content = await asyncio.to_thread(input_handler.handle_youtube, yt_id)
                return f"{metadata_header}{content}"
            except Exception as e:
                # Informative error suggesting local fallback if transcripts are unavailable
                raise RuntimeError(
                    f"No transcript available for YouTube video {yt_id}. "
                    f"Original error: {str(e)}. "
                    "Please use 'handle_yt_local' for this video to generate a transcript locally using Whisper."
                ) from e

        # 2. Routing: Local File Handling
        file_path = Path(input_val)
        ext = file_path.suffix.lower()
        handler = HANDLERS.get(ext)

        # Standard Metadata header for Markdown outputs
        if ext == ".pdf":
            # PDF handler manages its own metadata per page
            metadata_header = ""
        else:
            metadata_header = f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"

        if not handler:
            unsupported_header = f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n"
            return f"{unsupported_header}### [Warning: Unsupported format {ext}]\n\n"

        try:
            # Offload heavy CPU/OCR/Whisper work to a separate thread to prevent blocking the event loop
            content = await asyncio.to_thread(handler, file_path)
            return f"{metadata_header}{content}"
        except Exception as e:
            error_header = metadata_header or (f"\n---\nsource: {file_path.name}\ntype: {ext.lstrip('.')}\n---\n\n")
            return f"{error_header}### [Error processing file: {str(e)}]\n\n"


async def get_markdown(inputs: Iterable[str | Path]) -> List[str]:
    """The main conversion engine for a batch of files and YouTube URLs.

    Orchestration:
    - Smart Concurrency: Parallelizes small files, sequences large files (>200MB).
    - Resource Safety: Uses semaphores to cap memory/CPU usage.
    - Persistence: Saves results to the 'raw_data/' directory with collision-resistant naming.

    Args:
        inputs (Iterable[str | Path]): A collection of local file paths or YouTube URLs.

    Returns:
        List[str]: A list of absolute paths to the generated Markdown files in 'raw_data/'.
    """
    input_list = list(inputs)
    small_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    large_file_semaphore = asyncio.Semaphore(1)

    raw_data_dir = Path.cwd() / "raw_data"
    raw_data_dir.mkdir(parents=True, exist_ok=True)

    pending_tasks = []

    # Prepare and schedule all tasks based on input type and size
    for item in input_list:
        input_str = str(item)
        yt_id = input_handler.extract_youtube_id(input_str)

        if yt_id:
            # YouTube API calls are relatively low-resource (small tasks)
            pending_tasks.append(asyncio.create_task(_process_input(input_str, small_task_semaphore)))
            continue

        file_path = Path(item)
        if file_path.suffix.lower() not in allowed_extensions:
            # Immediate feedback for unsupported files
            error_msg = f"\n\n---\n### [Warning: Input not supported: {file_path.name}]\n---\n\n"
            f = asyncio.Future()
            f.set_result(error_msg)
            pending_tasks.append(f)
            continue

        # Resource Gate: Prevent OOM by sequencing large files
        file_size = file_path.stat().st_size
        if file_size <= MAX_PARALLEL_SIZE:
            pending_tasks.append(asyncio.create_task(_process_input(file_path, small_task_semaphore)))
        else:
            pending_tasks.append(asyncio.create_task(_process_input(file_path, large_file_semaphore)))

    output_paths = []
    # Collect results as they finish and persist them to disk
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

        # Increment suffix to prevent overwriting existing results in raw_data/
        counter = 1
        while out_path.exists():
            out_name = f"{base_name}_{counter}.md"
            out_path = raw_data_dir / out_name
            counter += 1

        with open(out_path, "w", encoding="utf-8") as md_file:
            md_file.write(result)

        output_paths.append(str(out_path))

    return output_paths


async def get_markdown_directory(directory_path: str | Path) -> List[str] | None:
    """Crawls a directory recursively and converts all supported files to Markdown.

    Ensures a deterministic, sorted order for consistent batch output.

    Args:
        directory_path (str | Path): Path to the source directory.

    Returns:
        List[str] | None: List of output file paths, or None if no supported files found.

    Raises:
        ValueError: If the provided path is not a valid directory.
    """
    dir_path = Path(directory_path)
    if not dir_path.is_dir():
        raise ValueError(f"The path {directory_path} is not a valid directory.")

    file_list = []
    for root, _, filenames in os.walk(dir_path):
        for filename in filenames:
            file_path = Path(root) / filename
            if file_path.suffix.lower() in allowed_extensions:
                file_list.append(file_path)

    if not file_list:
        return None

    # Sorting ensures that outputs like collisions (counter suffixes) are consistent across runs
    file_list.sort()
    return await get_markdown(file_list)


def handle_yt_local(urls: str | List[str]) -> List[str]:
    """Heavy-duty YouTube processor: Downloads videos and transcribes locally via Whisper.

    Use Case:
    - Use this when YouTube transcripts are disabled or unavailable.

    Workflow:
    1. Downloads video/audio using yt-dlp.
    2. Enforces a 200MB safety limit on downloads.
    3. Offloads transcription to input_handler.handle_video (Whisper).
    4. Automatically purges downloaded video files.

    Args:
        urls (str | List[str]): A single YouTube URL or a list of URLs.

    Returns:
        List[str]: A list of generated Markdown transcription strings.
    """
    if isinstance(urls, str):
        urls = [urls]

    transcriptions = []

    for url in urls:
        temp_dir = tempfile.gettempdir()
        # Random UUID prevents collisions in shared temp directories
        out_tmpl = str(Path(temp_dir) / f"yt_{uuid4()}.%(ext)s")

        # Configuration for downloading the best available format that works with FFmpeg
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

            # Security: Prevent rogue downloads from exhausting system resources
            file_size = Path(downloaded_file).stat().st_size
            if file_size > 200 * 1024 * 1024:
                Path(downloaded_file).unlink(missing_ok=True)
                raise ValueError("file is too large to process")

            # Orchestration: Reuse the existing video transcription pipeline
            transcription = input_handler.handle_video(downloaded_file)
            transcriptions.append(transcription)

            # Cleanup downloaded video immediately to free up disk space
            Path(downloaded_file).unlink(missing_ok=True)

        except Exception as e:
            transcriptions.append(f"\n\n### [Error processing YouTube video locally: {str(e)}]\n\n")

    return transcriptions
