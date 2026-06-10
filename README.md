# any-to-markdown

`any-to-markdown` is a lightweight Python package that converts a broad set of local files and YouTube links into Markdown.

It is designed for documentation pipelines, retrieval-augmented generation workflows, note taking, and bulk content normalization where the goal is to turn heterogeneous source material into a consistent Markdown representation.

**Author:** Sankalp Joshi  
**Version:** 0.1.0  
**License:** MIT

## What It Does

`any-to-markdown` converts supported inputs into Markdown and writes the generated output into a `raw_data/` directory in the current working directory.

The package supports:

- Plain text files
- JSON files
- Markdown files
- Word documents (`.docx`)
- Excel spreadsheets (`.xls`, `.xlsx`)
- PowerPoint presentations (`.pptx`)
- PDF files
- Images (`.png`, `.jpg`, `.jpeg`) via OCR
- Audio files (`.mp3`) via local transcription
- Video files (`.mp4`) via audio extraction and transcription
- YouTube links via transcript lookup
- YouTube links via local download and Whisper transcription when transcripts are unavailable

## Requirements

### Python

- Python `>=3.10`

### Python dependencies

The package depends on:

- `pandas`
- `pymupdf`
- `python-docx`
- `python-pptx`
- `faster-whisper`
- `python-multipart`
- `pytesseract`
- `pillow`
- `xlrd`
- `tabulate`
- `openpyxl`
- `youtube-transcript-api`
- `yt-dlp`

### External system tools

Some features require native tools to be installed on the host system:

- `ffmpeg` for video audio extraction and YouTube local transcription
- `tesseract` for OCR on images and OCR fallback for PDFs

Without these tools, the related handlers will fail at runtime.

## Public API

The package exports the following helpers from `any-to-markdown`:

- `get_markdown(inputs)`
- `get_markdown_directory(directory_path)`
- `handle_yt_local(urls)`

Import them like this:

```python
from any_to_markdown import get_markdown, get_markdown_directory, handle_yt_local
```

## Usage

### Convert a list of files or URLs

`get_markdown()` accepts an iterable of file paths and/or YouTube URLs.

It is asynchronous and returns a list of absolute output paths for the generated Markdown files.

```python
import asyncio
from any_to_markdown import get_markdown


async def main():
    outputs = await get_markdown([
        "docs/report.pdf",
        "slides/deck.pptx",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ])
    print(outputs)


asyncio.run(main())
```

### Convert a directory recursively

`get_markdown_directory()` walks a directory recursively, collects supported files, sorts them deterministically, and converts them.

```python
import asyncio
from any-to-markdown import get_markdown_directory


async def main():
    outputs = await get_markdown_directory("path/to/source-folder")
    print(outputs)


asyncio.run(main())
```

### Transcribe YouTube videos locally

Use `handle_yt_local()` when a YouTube transcript is unavailable or disabled.

This path downloads the video with `yt-dlp`, limits downloads to 200 MB, extracts audio with `ffmpeg`, and transcribes it with Whisper.

```python
from any-to-markdown import handle_yt_local

outputs = handle_yt_local([
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
])
print(outputs)
```

## Output Behavior

Generated Markdown files are written to:

```text
./raw_data/
```

File naming follows a predictable convention:

- Local files become `<stem>_<extension>.md`
- YouTube URLs become `youtube_<video_id>.md`
- If a file name already exists, a numeric suffix is appended to avoid overwriting

Examples:

- `report.pdf` -> `raw_data/report_pdf.md`
- `meeting.mp4` -> `raw_data/meeting_mp4.md`
- `https://youtu.be/<id>` -> `raw_data/youtube_<id>.md`

## Supported Inputs

### Text and structured files

- `.txt`
- `.json`
- `.md`
- `.docx`
- `.xls`
- `.xlsx`
- `.pptx`

### Media and OCR

- `.pdf`
- `.png`
- `.jpg`
- `.jpeg`
- `.mp3`
- `.mp4`

### YouTube

- Standard YouTube watch URLs
- `youtu.be` short URLs

## Processing Notes

If you intend to process images, PDFs with OCR, audio, video, or local YouTube transcriptions, you must install the external dependencies below.

### Installation of External Dependencies

#### Ubuntu / Debian

```bash
sudo apt update
sudo apt install ffmpeg tesseract-ocr
```

Verify installation:

```bash
ffmpeg -version
tesseract --version
```

---

#### Arch Linux

```bash
sudo pacman -S ffmpeg tesseract
```

---

#### Fedora

```bash
sudo dnf install ffmpeg tesseract
```

---

#### macOS (Homebrew)

```bash
brew install ffmpeg tesseract
```

---

#### Windows

##### FFmpeg

Download FFmpeg from:

<https://ffmpeg.org/download.html>

Extract it and add the `bin` directory to your system `PATH`.

Verify:

```powershell
ffmpeg -version
```

##### Tesseract OCR

Download Tesseract from:

<https://github.com/UB-Mannheim/tesseract/wiki>

Install it and ensure the installation directory is added to your system `PATH`.

Verify:

```powershell
tesseract --version
```

### Concurrency

`get_markdown()` processes smaller inputs concurrently and processes large files sequentially to reduce memory pressure.

- Files up to 200 MB can be processed in parallel
- Files larger than 200 MB are processed one at a time
- The package caps smaller-task concurrency to avoid overloading the machine

### Metadata headers

Most outputs begin with a small Markdown metadata block containing source information and input type. PDF pages are emitted with page-level metadata.

### PDFs

PDF handling attempts to preserve reading order, detect tables, and fall back to OCR when text is sparse or the page contains images.

### Word documents

DOCX tables and paragraphs are preserved in document order where possible.

### Spreadsheets

Each worksheet is emitted as its own Markdown table section.

### PowerPoint

Slides are exported slide by slide, including titles, text, tables, and speaker notes when present.

### Images

Images are processed with Tesseract OCR.

### Audio and video

Audio is transcribed locally using Faster-Whisper. Video files are first converted to audio with `ffmpeg`, then transcribed.

### YouTube transcript lookup

For YouTube URLs, the package first tries to fetch transcripts through `youtube-transcript-api`.

If transcript lookup fails, the error message recommends using `handle_yt_local()` for a local Whisper-based transcription workflow.

## Error Handling

The package favors continuing the batch workflow when possible:

- Unsupported file types are converted into warning blocks in the output list
- Handler failures are returned as Markdown error blocks where possible
- YouTube transcript failures raise a runtime error with a local-processing suggestion

This makes the package useful for large heterogeneous batches where partial success is better than failing the entire run.

## Example Workflow

```python
import asyncio
from any-to-markdown import get_markdown_directory


async def main():
    outputs = await get_markdown_directory("./input")
    if outputs is None:
        print("No supported files found.")
    else:
        for path in outputs:
            print(path)


asyncio.run(main())
```

## Project Structure

```text
src/any-to-markdown/
├── __init__.py
├── input_handler.py
└── main.py
```

## Notes For Integrators

- `get_markdown()` is asynchronous, so call it from an event loop or wrap it with `asyncio.run()`
- `handle_yt_local()` is synchronous and returns Markdown strings directly
- Outputs are always written to disk; the return value is the generated file path list
- The current implementation does not expose a CLI entry point

## Limitations

- OCR quality depends on the source image quality and local Tesseract configuration
- Whisper transcription requires the model download and enough local compute resources
- PDF table extraction depends on the structure of the input document
- Video processing depends on `ffmpeg` being available on the system path

## License

MIT License. See [LICENSE](LICENSE) for full terms.
