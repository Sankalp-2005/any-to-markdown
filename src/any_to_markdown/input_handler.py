"""Module for handling various input types and converting them to Markdown.

Supports text, documents, spreadsheets, presentations, images (OCR),
PDFs (with table detection), audio, video, and YouTube transcripts.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from uuid import uuid4

import fitz  # PyMuPDF
import pandas as pd
import pytesseract
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image
from pptx import Presentation
from youtube_transcript_api import YouTubeTranscriptApi

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

# Global model instance for Faster-Whisper to avoid reloading for every file
# Initialized lazily to save resources if no audio/video files are processed
_whisper_model: Optional[WhisperModel] = None
_model_lock = threading.Lock()


def get_whisper_model() -> WhisperModel:
    """Lazily initializes and returns the Faster-Whisper model.

    Architectural Decision:
    - Lazy initialization ensures we don't consume memory/GPU resources until needed.
    - Thread-safety is ensured via a global lock.
    - Uses GPU (CUDA) if available, otherwise falls back to CPU with int8 quantization.

    Returns:
         WhisperModel: An initialized instance of the Faster-Whisper model.
    """
    global _whisper_model
    with _model_lock:
        if _whisper_model is None:
            import ctranslate2
            from faster_whisper import WhisperModel

            # Auto-detect CUDA capability
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"

            # Optimization: float16 is significantly faster on modern GPUs,
            # while int8 is highly optimized for modern CPUs via ctranslate2.
            compute_type = "float16" if device == "cuda" else "int8"

            # Using 'small' model as a balance between speed and accuracy
            _whisper_model = WhisperModel("small", device=device, compute_type=compute_type)
    return _whisper_model


def handle_text(file_path: str | Path) -> str:
    """Reads plain text files (UTF-8) and returns content.

    Args:
        file_path: Path to the source text file.

    Returns:
        The file content wrapped in Markdown-friendly spacing.
    """
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as user_file:
        user_data = user_file.read()
    return f"\n\n{user_data}\n\n"


def handle_document(file_path: str | Path) -> str:
    """Processes DOCX files, preserving the logical order of paragraphs and tables.

    Implementation Detail:
    - Iterates through the document's XML body directly to ensure that
      tables are interleaved with text correctly.

    Args:
        file_path: Path to the .docx file.

    Returns:
        Consolidated Markdown content of the document.
    """
    user_doc = Document(file_path)
    parts: List[str] = []

    # Direct access to the body elements allows us to maintain strict document order
    for element in user_doc.element.body:
        if isinstance(element, CT_P):
            para = Paragraph(element, user_doc)
            if para.text.strip():
                parts.append(para.text + "\n\n")
        elif isinstance(element, CT_Tbl):
            table = Table(element, user_doc)
            table_data: List[List[str]] = []
            for row in table.rows:
                table_data.append([cell.text.strip() for cell in row.cells])

            if table_data:
                df = pd.DataFrame(table_data)
                # Heuristic: If multiple rows exist, assume the first row is a header
                if len(table_data) > 1:
                    df.columns = pd.Index(table_data[0])
                    df = df.iloc[1:]
                parts.append(df.to_markdown(index=False) + "\n\n")

    return "".join(parts)


def handle_excel(file_path: str | Path) -> str:
    """Processes Excel files (.xls, .xlsx).

    Args:
        file_path: Path to the Excel spreadsheet.

    Returns:
        All sheets converted into Markdown tables.
    """
    # Load all sheets to ensure no data is missed
    user_excel: Dict[str, pd.DataFrame] = pd.read_excel(file_path, sheet_name=None)
    parts: List[str] = []
    for sheet_name, df in user_excel.items():
        parts.append(f"### Sheet: {sheet_name}\n\n")
        parts.append(f"{df.to_markdown(index=False)}\n\n")
    return "".join(parts)


def handle_powerpoint(file_path: str | Path) -> str:
    """Processes PowerPoint files, extracting text slide by slide.

    Extracts titles, text boxes, tables, and speaker notes.

    Args:
        file_path: Path to the .pptx file.

    Returns:
        Segmented Markdown content per slide.
    """
    ppt = Presentation(file_path)
    parts: List[str] = []
    for i, slide in enumerate(ppt.slides, start=1):
        parts.append(f"## Slide {i}\n\n")

        # Extract slide title as a level 3 header
        title = slide.shapes.title
        if title and hasattr(title, "text") and title.text.strip():
            parts.append(f"### {title.text.strip()}\n\n")

        # Process all shapes (text boxes, tables, etc.)
        for shape in slide.shapes:
            if shape == title:
                continue

            if hasattr(shape, "text") and shape.text.strip():
                parts.append(shape.text.strip() + "\n\n")

            if shape.has_table:
                table_data: List[List[str]] = []
                for row in shape.table.rows:
                    table_data.append([cell.text_frame.text.strip() for cell in row.cells])
                df = pd.DataFrame(table_data)
                parts.append(df.to_markdown(index=False) + "\n\n")

        # Capture speaker notes to provide context often missed in raw slides
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"*Speaker Notes:* {notes}\n\n")

    return "".join(parts)


def handle_image(file_path: str | Path) -> str:
    """Uses Tesseract OCR to extract text from images.

    Args:
        file_path: Path to the image file.

    Returns:
        The extracted text content.
    """
    with Image.open(file_path) as image:
        # Pre-processing for better OCR accuracy
        grayscale = image.convert("L")
        text: str = pytesseract.image_to_string(grayscale, config="--psm 6")
    return text.strip()


def _get_pdf_items(page: fitz.Page, ignore_bboxes: Optional[List[fitz.Rect]] = None) -> List[Tuple[float, str]]:
    """Internal helper for PDF extraction that preserves reading order.

    Args:
        page: The PyMuPDF page object.
        ignore_bboxes: Regions to exclude (e.g., tables).

    Returns:
        List of (y_coordinate, content) tuples.
    """
    if ignore_bboxes is None:
        ignore_bboxes = []

    blocks = page.get_text("dict")["blocks"]
    items: List[Tuple[float, str]] = []

    for b in blocks:
        if b["type"] == 0:  # text block
            # Spatial exclusion: Skip text that overlaps with detected table regions
            if any(fitz.Rect(b["bbox"]).intersects(ib) for ib in ignore_bboxes):
                continue

            y = b["bbox"][1]
            structured_parts: List[str] = []
            for l in b["lines"]:  # noqa: E741
                for s in l["spans"]:
                    text = s["text"].strip()
                    if not text:
                        continue

                    size = s["size"]
                    flags = s["flags"]

                    # Heuristic for Markdown conversion based on PDF styling
                    if size > 16:
                        structured_parts.append(f"\n# {text}\n")
                    elif size > 13:
                        structured_parts.append(f"\n## {text}\n")
                    elif flags & 2:  # bold flag bit in PyMuPDF
                        structured_parts.append(f"\n### {text}\n")
                    else:
                        structured_parts.append(text + " ")
                structured_parts.append("\n")
            items.append((y, "".join(structured_parts)))
    return items


def handle_pdf(file_path: str | Path, use_layout_engine: bool = False) -> str:
    """Advanced PDF handler using PyMuPDF and OCR fallback.

    Args:
        file_path: Path to the .pdf file.
        use_layout_engine: Whether to use advanced PDF layout analysis.

    Returns:
        The structured Markdown content of the PDF.
    """
    if use_layout_engine:
        try:
            import pymupdf4llm
            return pymupdf4llm.to_markdown(str(file_path))
        except ImportError:
            # Silently fallback if the extra isn't installed
            pass

    parts: List[str] = []
    path = Path(file_path)
    file_name = path.name
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            parts.append(f"---\nsource: {file_name}\npage: {page_no}\ntype: pdf\n---\n\n")

            # 1. Spatial Table Detection
            tabs = page.find_tables()
            table_bboxes = [t.bbox for t in tabs.tables]

            # 2. Extract Text while excluding table areas to prevent duplicate data
            items = _get_pdf_items(page, ignore_bboxes=table_bboxes)

            # 3. Add Table data to the sortable items list
            for tab in tabs.tables:
                df = tab.to_pandas()
                if not df.empty:
                    # Use the top Y-coordinate for correct interleaving
                    items.append((tab.bbox[1], f"\n\n{df.to_markdown(index=False)}\n\n"))

            # 4. Global sort to reconstruct the page layout accurately
            items.sort(key=lambda x: x[0])
            text = "".join(item[1] for item in items)

            if text.strip():
                parts.append(text + "\n\n")

            # 5. Visual/OCR Fallback: Trigger if text is sparse or images are present
            if len(text.strip()) < 100 or len(page.get_images()) > 0:
                with tempfile.NamedTemporaryFile(suffix=f"_page_{page_no}.png", delete=False) as temp_image:
                    img_path = Path(temp_image.name)

                try:
                    # Render the page at 2x zoom for high-quality OCR
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    pix.save(img_path)
                    ocr_text = handle_image(img_path)
                    if ocr_text and ocr_text not in text:
                        parts.append(f"\n> [Visual Content OCR]:\n> {ocr_text}\n\n")
                finally:
                    img_path.unlink(missing_ok=True)
    return "".join(parts)


def handle_notebook(file_path: str | Path) -> str:
    """Parses Jupyter Notebooks (.ipynb) and converts cells to Markdown.

    Args:
        file_path: Path to the .ipynb file.

    Returns:
        Consolidated Markdown content.
    """
    import nbformat

    path = Path(file_path)
    with path.open("r", encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    parts: List[str] = []
    for cell in nb.cells:
        if cell.cell_type == "markdown":
            parts.append(cell.source + "\n\n")
        elif cell.cell_type == "code":
            # Default to python if language not specified in metadata
            lang: str = nb.metadata.get("language_info", {}).get("name", "python")
            parts.append(f"```{lang}\n{cell.source}\n```\n\n")
    return "".join(parts)


def handle_code(file_path: str | Path) -> str:
    """Reads source code files and wraps them in Markdown code blocks.

    Args:
        file_path: Path to the source file.

    Returns:
        The content wrapped in a fenced code block with language detection.
    """
    ext_to_lang = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".rb": "ruby",
        ".php": "php",
        ".sh": "bash",
        ".sql": "sql",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".md": "markdown",
    }
    path = Path(file_path)
    ext = path.suffix.lower()
    lang = ext_to_lang.get(ext, ext.lstrip(".") or "text")

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    return f"```{lang}\n{content}\n```\n\n"


def handle_audio(file_path: str | Path) -> str:
    """Transcribes audio files using the local Faster-Whisper engine.

    Args:
        file_path: Path to the audio file.

    Returns:
        The generated transcript text.
    """
    whisper_model = get_whisper_model()
    # segments is a generator; we consume it to get the full transcript
    segments, _ = whisper_model.transcribe(str(file_path), vad_filter=True)
    return "".join(segment.text + "\n" for segment in segments)


def handle_video(file_path: str | Path) -> str:
    """Processes video files by extracting the audio stream and transcribing it.

    Args:
        file_path: Path to the video file.

    Returns:
        The generated transcript text extracted from the video's audio.
    """
    audio_path = Path(tempfile.gettempdir()) / f"temp_{uuid4()}.mp3"

    try:
        # Extract the audio track without re-encoding video to save time
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(file_path),
                "-q:a",
                "0",
                "-map",
                "a",
                str(audio_path),
                "-y",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return handle_audio(audio_path)
    finally:
        # Guarantee cleanup of the heavy audio file
        audio_path.unlink(missing_ok=True)


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts the 11-character YouTube video ID using a robust regex.

    Args:
        url: The YouTube URL or potential ID.

    Returns:
        The extracted 11-char ID, or None if no match found.
    """
    regex = r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})"
    match = re.search(regex, url)
    return match.group(1) if match else None


def handle_youtube(video_id_or_url: str) -> str:
    """Fetches transcripts for YouTube videos via the internal API.

    Args:
        video_id_or_url: A YouTube URL or 11-character video ID.

    Returns:
        The transcript text fetched from YouTube.

    Raises:
        ValueError: If the transcript cannot be retrieved for any reason.
    """
    video_id = extract_youtube_id(video_id_or_url) or video_id_or_url
    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)

        try:
            # Priority 1: English
            transcript = transcript_list.find_transcript(["en"])
        except Exception:
            # Priority 2: Any available language
            transcript = next(iter(transcript_list))

        data: List[Dict[str, Any]] = transcript.fetch()
        text = " ".join(chunk["text"] for chunk in data)
        return f"\n\n{text}\n\n"

    except Exception as e:
        # Propagate error for the UI/Main logic to suggest local processing
        raise ValueError(str(e)) from e
