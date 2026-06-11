# any-to-markdown

`any-to-markdown` is a lightweight Python package that converts a broad set of local files and YouTube links into Markdown.

It is designed for documentation pipelines, retrieval-augmented generation (RAG) workflows, and any scenario where you need to normalize diverse data sources into clean, structured text.

**Author:** Sankalp Joshi  
**License:** MIT

---

## Key Features

- **Broad File Support:** Converts PDF, DOCX, PPTX, XLSX, Jupyter Notebooks (.ipynb), Images (OCR), Audio/Video (Transcription), and virtually any source code file.
- **Advanced PDF Engine:** Built-in AI-powered layout analysis to remove "noise" (headers, footers, page numbers) and accurately extract tables.
- **YouTube Integration:** Fetches transcripts directly via API or transcribes video locally using Whisper.
- **Smart Concurrency:** Automatically manages resource usage, processing large files sequentially and small files in parallel.
- **Secure & Private:** Sanitizes error messages to prevent leaking system paths and sensitive information.
- **No Overwrites:** Saves results to a `raw_data/` directory with collision-resistant naming.

## Supported Formats

- **Documents:** `.pdf`, `.docx`, `.pptx`, `.txt`, `.md`
- **Jupyter Notebooks:** `.ipynb` (Extracts Markdown and Code cells)
- **Source Code:** `.py`, `.js`, `.ts`, `.cpp`, `.c`, `.rs`, `.go`, `.java`, `.rb`, `.php`, `.sh`, `.sql`, `.html`, `.css`, `.yaml`, `.json`, `.xml`, etc.
- **Data:** `.xlsx`, `.xls`, `.csv`
- **Images:** `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp` (via OCR)
- **Multimedia:** `.mp3`, `.mp4`, `.m4a`, `.wav` (via Transcription)
- **Web:** YouTube URLs (Transcripts)

## Installation

```bash
pip install any-to-markdown
```

### External Dependencies

For full functionality, ensure the following are installed on your system:

- **FFmpeg:** Required for audio/video processing and local YouTube transcription.
- **Tesseract OCR:** Required for image OCR and PDF visual fallback.

---

## Public API

The package exports the following helpers from `any_to_markdown`:

- `get_markdown(inputs, use_layout_engine=False)`
- `get_markdown_directory(directory_path, use_layout_engine=False)`
- `handle_yt_local(urls)`

### Advanced PDF Layout

For significantly better PDF conversion (smart table detection, header/footer removal), enable the advanced layout engine:

```python
results = await get_markdown("input.pdf", use_layout_engine=True)
```

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

Use `handle_yt_local()` when a YouTube transcript is unavailable or disabled. This downloads the audio and transcribes it locally using Whisper.

```python
from any_to_markdown import handle_yt_local

# handle_yt_local is synchronous
outputs = handle_yt_local("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(outputs[0]) # Returns the raw markdown string
```

---

## Output Behavior

Generated Markdown files are written to a `./raw_data/` directory in the current working directory.

- **Local Files:** `<filename>_<extension>.md` (e.g., `data.csv` -> `data_csv.md`)
- **YouTube:** `youtube_<video_id>.md`
- **Collisions:** If a file exists, a numeric suffix is added (e.g., `report_pdf_1.md`).

---

## Troubleshooting & Tips

- **Large Files:** Files > 200MB are automatically processed sequentially to prevent memory issues.
- **OCR Quality:** Depends on your local Tesseract installation and image resolution.
- **Whisper Performance:** On the first run, the Whisper model will be downloaded (cached locally). CPU performance is optimized using `int8` quantization.
- **Privacy:** Errors caught during processing are sanitized to remove absolute local paths before being written to Markdown.

## License

MIT License. See [LICENSE](LICENSE) for full terms.
