# any-to-markdown

`any-to-markdown` is a lightweight Python package that converts a broad set of local files and YouTube links into Markdown.

It is designed for documentation pipelines, retrieval-augmented generation (RAG) workflows, and any scenario where you need to normalize diverse data sources into clean, structured text.

**Author:** Sankalp Joshi  
**License:** MIT

---

## Key Features

- **Broad File Support:** Converts PDF, DOCX, PPTX, XLSX, Jupyter Notebooks (.ipynb), Images (OCR), Audio/Video (Transcription), and many source code file types.
- **Optional PDF Layout Mode:** Layout analysis via `pymupdf4llm` heuristics (table detection and structure-aware Markdown). The default PDF engine uses PyMuPDF with font-size and bold-flag heuristics, plus an OCR fallback for image-heavy pages.
- **YouTube Integration:** Fetches transcripts directly via the YouTube transcript API, or transcribes locally with Whisper via `handle_yt_local` (audio-only download, no FFmpeg required).
- **Honest Concurrency:** Small files run in parallel, files over 200MB run sequentially, and Whisper transcription jobs are limited to one at a time by default (see Concurrency Model below).
- **Secure & Private:** Sanitizes error messages to prevent leaking system paths and sensitive information.
- **No Overwrites:** Saves results to a `raw_data/` directory with collision-resistant naming.

## Supported Formats

This list matches the code's `ALLOWED_EXTENSIONS` exactly:

- **Documents:** `.pdf`, `.docx`, `.pptx`, `.txt`, `.md`
- **Jupyter Notebooks:** `.ipynb` (extracts Markdown and code cells)
- **Source Code & Markup:** `.py`, `.js`, `.ts`, `.cpp`, `.c`, `.h`, `.hpp`, `.rs`, `.go`, `.java`, `.rb`, `.php`, `.sh`, `.sql`, `.yaml`, `.yml`, `.json`, `.xml`, `.html`, `.css`
- **Data:** `.xlsx`, `.xls`, `.csv`
- **Images (OCR):** `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`
- **Multimedia (Transcription):** `.mp3`, `.wav`, `.m4a`, `.mp4`
- **Web:** YouTube URLs (transcripts)

## Installation

```bash
pip install any-to-markdown
```

All Python dependencies, including `pymupdf4llm` for the optional layout mode, are installed by default. There are currently no optional extras.

### External Dependencies

- **Tesseract OCR:** Required for image OCR and the PDF visual fallback.
- **FFmpeg:** Required only for local video files (`.mp4`), where the audio track is extracted before transcription. It is **not** required for `handle_yt_local`, which downloads an audio-only stream and feeds it directly to Whisper.

---

## Public API

The package exports the following helpers from `any_to_markdown`:

- `get_markdown(inputs, use_layout_engine=False, max_transcriptions=1)`
- `get_markdown_directory(directory_path, use_layout_engine=False, max_transcriptions=1)`
- `handle_yt_local(urls)`

### PDF Layout Mode

For PDFs with complex tables, enable the `pymupdf4llm`-based layout analysis:

```python
results = await get_markdown("input.pdf", use_layout_engine=True)
```

This mode is heuristic-based (not AI-powered): it relies on `pymupdf4llm`'s rules for detecting tables, headings, and document structure.

---

## Usage Examples

### Convert a list of files or URLs

`get_markdown()` is asynchronous and accepts a single path/URL or a list of them.

```python
import asyncio
from any_to_markdown import get_markdown

async def main():
    outputs = await get_markdown([
        "docs/report.pdf",
        "analysis.ipynb",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ], use_layout_engine=True)

    for path in outputs:
        print(f"Generated: {path}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Convert a directory recursively

```python
import asyncio
from any_to_markdown import get_markdown_directory

async def main():
    # Automatically finds and processes all supported files in the folder
    outputs = await get_markdown_directory("./my_docs", use_layout_engine=True)
    print(f"Processed {len(outputs)} files.")

if __name__ == "__main__":
    asyncio.run(main())
```

### Transcribe YouTube videos locally

Use `handle_yt_local()` when a YouTube transcript is unavailable or disabled. It downloads the **audio-only** stream (`bestaudio[ext=m4a]/bestaudio/best`) and transcribes it locally with Whisper. No FFmpeg is needed for this path.

```python
from any_to_markdown import handle_yt_local

# handle_yt_local is synchronous
outputs = handle_yt_local("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(outputs[0]) # Returns the raw markdown string
```

---

## Output Behavior

`get_markdown` and `get_markdown_directory` always **write Markdown files to disk** and return the list of generated file paths. They do not return the Markdown content itself, and there is currently no `output_dir` parameter: files are written to a `./raw_data/` directory in the current working directory (created automatically).

- **Local Files:** `<filename>_<extension>.md` (e.g., `data.csv` -> `data_csv.md`)
- **YouTube:** `youtube_<video_id>.md`
- **Collisions:** If a file exists, a numeric suffix is added (e.g., `report_pdf_1.md`).

`handle_yt_local` is the exception: it returns the Markdown transcription strings directly and writes nothing to disk.

---

## Concurrency Model

- Up to **10** small files are processed concurrently.
- Files larger than **200MB** are processed sequentially (one at a time) to avoid out-of-memory errors.
- Audio and video files (`.mp3`, `.wav`, `.m4a`, `.mp4`) are always routed through a dedicated transcription semaphore so that only **one Whisper job** runs at a time by default, regardless of file size. Tune this with the `max_transcriptions` parameter.

## Troubleshooting & Tips

- **OCR Quality:** Depends on your local Tesseract installation and image resolution.
- **Whisper Performance:** On the first run, the Whisper model will be downloaded (cached locally). CPU performance is optimized using `int8` quantization.
- **Privacy:** Errors caught during processing are sanitized to remove absolute local paths before being written to Markdown.

## License

MIT License. See [LICENSE](LICENSE) for full terms.
