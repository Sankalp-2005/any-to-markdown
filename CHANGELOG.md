# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.3] - 2026-06-21

### Fixed

- **PDF batch processing hang:** Processing large batches (200+ PDFs) no longer
  stalls after roughly 12 files. The root cause was CPU saturation from too
  many parallel PDFs sharing one worker pool. PDF conversions now use a
  dedicated semaphore.
- **No more OCR data loss:** The previous 10-OCR-page-per-PDF cap and 120s
  per-PDF budget silently dropped content from long scanned documents (sparse
  pages past the cap got no text *and* no OCR). Both are removed. OCR now runs
  on every sparse page so documents convert in full. Only a per-page timeout
  (`ANY_TO_MARKDOWN_OCR_TIMEOUT`, default 60s) remains — purely a reactive net
  to kill a wedged process, never a content skip. The default was raised from
  10s after measuring dense numeric-table pages that legitimately take 30-50s
  (median page is ~1s).
- **Tables on by default:** Built-in PyMuPDF table detection now runs by
  default, so PDFs exported from spreadsheets (e.g. Excel) keep their tables.
  Safe because the original hang was concurrency saturation, not tables.
  Disable with `extract_pdf_tables=False`, `--no-pdf-tables`, or
  `ANY_TO_MARKDOWN_PDF_TABLES=0`.
- Non-positive PDF/transcription concurrency values now fail fast with
  `ValueError` instead of creating a semaphore that waits forever.
- Structured PDF extraction failures degrade to raw text instead of aborting
  the page or batch.

### Changed

- PDF concurrency defaults to **half the CPU cores** (`MAX_CONCURRENT_PDFS`),
  since `find_tables()` and Tesseract are each single-threaded CPU work. Tunable
  via `max_pdf_tasks` / `--max-pdf-tasks`. (Previously a fixed 2.)

## [0.2.2] - 2026-06-20

### Fixed

- **PDF OCR fallback performance:** The OCR fallback previously triggered on
  every page that contained *any* image (logos, charts, decorations), causing
  extreme slowness on image-rich but text-normal PDFs. It now only fires when
  the extracted text is truly sparse (< 100 characters), correctly limiting OCR
  to genuinely scanned or image-based pages.
- **Broader OCR error resilience:** The OCR fallback now catches all exceptions
  (not just `MissingDependencyError`), so issues like a missing Tesseract
  binary, processing errors, or unexpected failures never abort the entire PDF
  conversion. A per-page warning is emitted instead.
- **Improved OCR warning messages:** OCR fallback warnings now include the page
  number for easier debugging (e.g. `Skipping OCR fallback for page 3: ...`).

## [0.2.1] - 2026-06-19

### Added

- Live terminal progress bars during batch conversion. All four public batch
  functions — `get_markdown`, `get_markdown_directory`, `handle_yt_local`, and
  `handle_yt_local_async` — now render a per-input `rich` progress bar to stderr
  (e.g. `Converting 3/206 • 0:00:12`) while the conversion work is in flight.
- New `show_progress: bool = True` parameter on every batch function. Pass
  `show_progress=False` to suppress the bar from library code.
- `ANY_TO_MARKDOWN_NO_PROGRESS` environment variable: set it to any truthy value
  (`1`, `true`, `yes`, …) to disable the bar globally without code changes.
- Automatic suppression when stderr is not a TTY (piped output, redirected
  logs, CI captures), so the bar never pollutes non-interactive runs.
- `rich>=13.7.0` added as a core dependency.

### Fixed

- `handle_yt_local_async` previously produced no feedback during long
  transcriptions; the new progress bar advances once per completed URL.

## [0.2.0] - 2026-06-13

### Added

- `any-to-markdown` command-line interface (typer-based): convert files, directories,
  and YouTube URLs with `--output-dir/-o`, `--layout`, `--max-transcriptions`,
  `--whisper-model`, and `--version`. Exits non-zero if any input errored.
- Real HTML-to-Markdown conversion for `.html` and `.htm` files using a stdlib
  `html.parser`-based converter (headings, paragraphs, emphasis, links, nested
  lists, tables, code/pre blocks, blockquotes; `<script>`/`<style>` stripped).
- Configurable Whisper model size via the `whisper_model` parameter on
  `get_markdown`, `get_markdown_directory`, and `handle_yt_local`, or the
  `ANY_TO_MARKDOWN_WHISPER_MODEL` environment variable. Model instances are
  cached per size.
- `handle_yt_local_async`: concurrent YouTube transcription with an optional
  `output_dir` that writes `youtube_<video_id>.md` files. The synchronous
  `handle_yt_local` wrapper is preserved.
- `ConversionStatus` Literal type for `ConversionResult.status`.
- `py.typed` marker so downstream type checkers consume the package's annotations.
- CI: Python 3.10-3.13 test matrix, `ruff format --check`, test coverage reporting,
  and a tag-triggered PyPI publish job using Trusted Publishing.

### Changed

- **Breaking:** `get_markdown_directory` returns an empty list instead of `None`
  when the directory contains no supported files.
- `handle_text` now decodes UTF-8 (BOM-aware) with a lossless Latin-1 fallback
  instead of failing on the first non-UTF-8 byte.
- Error sanitization preserves URLs verbatim; absolute filesystem paths are
  still masked to their filename.
- `extract_youtube_id` is computed exactly once per input instead of up to
  three times.

### Fixed

- `handle_yt_local`'s download size cap is now its own constant
  (`MAX_DOWNLOAD_SIZE`) instead of silently reusing the concurrency threshold
  (`MAX_PARALLEL_SIZE`).

### Removed

- Dead `python-multipart` dependency.

## [0.1.0]

- Initial release.
