# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-12

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

## [0.2.0] - 2026-06-12

### Added

- Structured `ConversionResult` API: every input yields an explicit
  `success`/`error`/`skipped` result with content, output path, sanitized
  error, and suggestion fields.
- Dependency extras `[pdf]`, `[ocr]`, `[audio]`, `[youtube]`, `[all]`; the core
  install stays small. Missing extras raise an actionable
  `MissingDependencyError`.
- `__version__` resolved from package metadata via `importlib.metadata`.

### Removed

- Dead `python-multipart` dependency.

## [0.1.0]

- Initial release.
