# any-to-markdown

`any-to-markdown` is a lightweight Python package that converts a broad set of local files and YouTube links into Markdown.

It is designed for documentation pipelines, retrieval-augmented generation (RAG) workflows, and any scenario where you need to normalize diverse data sources into clean, structured text.

**Author:** Sankalp Joshi  
**License:** MIT

---

## Key Features

- **Broad File Support:** Converts PDF, DOCX, PPTX, XLSX, Jupyter Notebooks (.ipynb), Images (OCR), Audio/Video (Transcription), and many source code file types.
- **Structured Results:** Every input yields a `ConversionResult` with an explicit `success` / `error` / `skipped` status, the Markdown content, the output path, and a machine-readable error. No more parsing error prose out of markdown.
- **Batch Resilience:** One failed input never aborts the batch. Failures emit warnings naming the offending file, with a batch summary and a suggested alternative function.
- **Opt-in Heavy Dependencies:** PDF, OCR, audio transcription, and YouTube support are pip extras; the core install stays small.
- **Optional PDF Layout Mode:** Layout analysis via `pymupdf4llm` heuristics (table detection and structure-aware Markdown). The default PDF engine uses PyMuPDF with font-size and bold-flag heuristics, plus an OCR fallback for image-heavy pages.
- **YouTube Integration:** Fetches transcripts directly via the YouTube transcript API, or transcribes locally with Whisper via `handle_yt_local` (audio-only download, no FFmpeg required).
- **Honest Concurrency:** Small files run in parallel, files over 200MB run sequentially, and Whisper transcription jobs are limited to one at a time by default.
- **Secure & Private:** Sanitizes error messages to prevent leaking system paths and sensitive information.
- **No Overwrites:** Collision-resistant, race-free output naming.

## Supported Formats

This list matches the code's `ALLOWED_EXTENSIONS` exactly (derived directly from the handler registry):

- **Documents:** `.pdf`, `.docx`, `.pptx`, `.txt`, `.md`
- **Jupyter Notebooks:** `.ipynb` (extracts Markdown and code cells)
- **Source Code & Markup:** `.py`, `.js`, `.ts`, `.cpp`, `.c`, `.h`, `.hpp`, `.rs`, `.go`, `.java`, `.rb`, `.php`, `.sh`, `.sql`, `.yaml`, `.yml`, `.json`, `.xml`, `.html`, `.css`
- **Data:** `.xlsx`, `.xls`, `.csv`
- **Images (OCR):** `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`
- **Multimedia (Transcription):** `.mp3`, `.wav`, `.m4a`, `.mp4`
- **Web:** YouTube URLs (transcripts)

## Installation

The core install covers text, code, notebooks, CSV/Excel, DOCX, and PPTX:

```bash
pip install any-to-markdown
```

Heavy capabilities are opt-in extras:

```bash
pip install any-to-markdown[pdf]      # PDF conversion (PyMuPDF + pymupdf4llm)
pip install any-to-markdown[ocr]      # Image OCR (pytesseract + Pillow)
pip install any-to-markdown[audio]    # Audio/video transcription (faster-whisper)
pip install any-to-markdown[youtube]  # YouTube transcripts + local download
pip install any-to-markdown[all]      # Everything
```

If you call a handler without its extra installed, you get a clear `MissingDependencyError` telling you the exact `pip install` command to run.

### External Dependencies

- **Tesseract OCR:** Required for image OCR and the PDF visual fallback (the PDF engine degrades gracefully with a warning if OCR is unavailable).
- **FFmpeg:** Required only for local video files (`.mp4`). It is **not** required for `handle_yt_local`, which downloads an audio-only stream and feeds it directly to Whisper.

---

## Public API

The package exports the following from `any_to_markdown`:

- `get_markdown(inputs, use_layout_engine=False, max_transcriptions=1, output_dir=None)`
- `get_markdown_directory(directory_path, use_layout_engine=False, max_transcriptions=1, output_dir=None)`
- `handle_yt_local(urls)`
- `ConversionResult`, `MissingDependencyError`, `TranscriptUnavailableError`

### ConversionResult

Every conversion function returns one `ConversionResult` per input:

| Field | Meaning |
|---|---|
| `input` | The original path or URL |
| `status` | `"success"`, `"error"`, or `"skipped"` |
| `ok` | Convenience property, `True` on success |
| `content` | The generated Markdown (on success) |
| `output_path` | `Path` to the written `.md` file (default output mode) |
| `message` | Human-readable success message (when you pass `output_dir`) |
| `error` | Sanitized, machine-readable error description |
| `suggestion` | Suggested alternative, e.g. `handle_yt_local` for failed transcripts |

### PDF Layout Mode

For PDFs with complex tables, enable the `pymupdf4llm`-based layout analysis:

```python
results = await get_markdown("input.pdf", use_layout_engine=True)
```

This mode is heuristic-based (not AI-powered): it relies on `pymupdf4llm`'s rules for detecting tables, headings, and document structure.

---

## Usage Examples

### Convert a list of files or URLs

```python
import asyncio
from any_to_markdown import get_markdown

async def main():
    results = await get_markdown([
        "docs/report.pdf",
        "analysis.ipynb",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ], use_layout_engine=True)

    for result in results:
        if result.ok:
            print(f"Generated: {result.output_path}")  # pathlib.Path
        else:
            print(f"{result.status}: {result.input} -> {result.error}")
            if result.suggestion:
                print(f"  Try: {result.suggestion}()")

if __name__ == "__main__":
    asyncio.run(main())
```

### Custom output directory

```python
results = await get_markdown("docs/report.pdf", output_dir="converted/")
print(results[0].message)
# Success: 'docs/report.pdf' converted and written to 'converted/report_pdf.md'
```

### Convert a directory recursively

```python
import asyncio
from any_to_markdown import get_markdown_directory

async def main():
    results = await get_markdown_directory("./my_docs", use_layout_engine=True)
    if results:
        print(f"{sum(r.ok for r in results)} of {len(results)} files converted.")

if __name__ == "__main__":
    asyncio.run(main())
```

### Transcribe YouTube videos locally

Use `handle_yt_local()` when a YouTube transcript is unavailable or disabled. It downloads the **audio-only** stream (`bestaudio[ext=m4a]/bestaudio/best`) and transcribes it locally with Whisper. No FFmpeg is needed for this path, and nothing is written to disk.

```python
from any_to_markdown import handle_yt_local

results = handle_yt_local("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
if results[0].ok:
    print(results[0].content)  # The Markdown transcription string
```

---

## Output Behavior

- **Default:** files are written to `./raw_data/` in the current working directory (created only when at least one input succeeds), and each successful result carries the `Path` object in `output_path` plus the Markdown in `content`.
- **With `output_dir`:** files are written to your directory, and each successful result additionally carries a human-readable `message`.
- **Naming:** `<filename>_<extension>.md` for local files, `youtube_<video_id>.md` for videos. Collisions get a numeric suffix (e.g. `report_pdf_1.md`) using race-free exclusive-create writes.
- Failed or skipped inputs never produce output files and never embed error prose into Markdown.

## Error Handling

- Each failed input produces a result with `status="error"`, a sanitized `error` string, and (where applicable) a `suggestion` such as `handle_yt_local` for unavailable YouTube transcripts.
- A `UserWarning` is emitted per failure naming the exact input, plus a batch summary: how many succeeded, failed, and were skipped.
- One bad input never aborts the batch.

## Concurrency Model

- Up to **10** small files are processed concurrently.
- Files larger than **200MB** are processed sequentially to avoid out-of-memory errors.
- Audio and video files (`.mp3`, `.wav`, `.m4a`, `.mp4`) always go through a dedicated transcription semaphore so only **one Whisper job** runs at a time by default. Tune this with `max_transcriptions`.

## Troubleshooting & Tips

- **OCR Quality:** Depends on your local Tesseract installation and image resolution.
- **Whisper Performance:** On the first run, the Whisper model will be downloaded (cached locally). CPU performance is optimized using `int8` quantization.

## License

MIT License. See [LICENSE](LICENSE) for full terms.
