"""Tests for individual file handlers."""

from __future__ import annotations

from pathlib import Path

import fitz

from any_to_markdown import input_handler

# Filler lines keep the extracted page text above the OCR-fallback threshold
# (100 chars) used by handle_pdf, so these tests do not require Tesseract.
_FILLER_LINES = [
    "This is plain body text used to keep the page",
    "text above the OCR fallback threshold so that",
    "these tests do not require Tesseract locally.",
]


def _make_pdf(path: Path, text: str, fontname: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontname=fontname, fontsize=11)
    for i, line in enumerate(_FILLER_LINES):
        page.insert_text((72, 144 + i * 16), line, fontname="helv", fontsize=11)
    doc.save(str(path))
    doc.close()


def test_handle_pdf_bold_text_becomes_heading(tmp_path: Path) -> None:
    pdf_path = tmp_path / "bold.pdf"
    _make_pdf(pdf_path, "Bold Statement", "hebo")  # Helvetica-Bold

    result = input_handler.handle_pdf(pdf_path)

    assert "### Bold Statement" in result


def test_handle_pdf_italic_text_is_extracted(tmp_path: Path) -> None:
    pdf_path = tmp_path / "italic.pdf"
    _make_pdf(pdf_path, "Italic Statement", "heit")  # Helvetica-Oblique

    result = input_handler.handle_pdf(pdf_path)

    # Italic text has no special Markdown mapping, but it must not be lost
    # and must not be promoted to a heading.
    assert "Italic Statement" in result
    assert "### Italic Statement" not in result


def test_handle_pdf_detects_tables_by_default(tmp_path: Path, monkeypatch) -> None:
    """Tables are detected by default so spreadsheet PDFs keep their tables."""
    pdf_path = tmp_path / "default-tables.pdf"
    _make_pdf(pdf_path, "Default PDF Path", "hebo")
    original_find_tables = fitz.Page.find_tables
    calls = 0

    def track_call(page, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_find_tables(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "find_tables", track_call)

    result = input_handler.handle_pdf(pdf_path)  # no extract_tables arg

    assert calls == 1, "find_tables must run by default"
    assert "### Default PDF Path" in result


def test_handle_pdf_table_detection_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    """extract_tables=False must skip find_tables even though it's the default."""
    pdf_path = tmp_path / "no-tables.pdf"
    _make_pdf(pdf_path, "Opted Out", "hebo")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("find_tables must not run when extract_tables=False")

    monkeypatch.setattr(fitz.Page, "find_tables", fail_if_called)

    result = input_handler.handle_pdf(pdf_path, extract_tables=False)

    assert "### Opted Out" in result


def test_handle_pdf_table_detection_env_var_disables(tmp_path: Path, monkeypatch) -> None:
    """ANY_TO_MARKDOWN_PDF_TABLES=0 must turn off table detection by default."""
    pdf_path = tmp_path / "env-off.pdf"
    _make_pdf(pdf_path, "Env Disabled", "hebo")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("find_tables must not run when env var disables it")

    monkeypatch.setattr(fitz.Page, "find_tables", fail_if_called)
    monkeypatch.setenv("ANY_TO_MARKDOWN_PDF_TABLES", "0")

    # Re-read the module-level default so the env override takes effect.
    monkeypatch.setattr(input_handler, "_PDF_TABLE_DETECTION_ENABLED_BY_DEFAULT", False)

    result = input_handler.handle_pdf(pdf_path)

    assert "Env Disabled" in result


def test_handle_csv_produces_markdown_table(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("name,age\nalice,30\nbob,25\n", encoding="utf-8")

    result = input_handler.handle_csv(csv_path)

    assert "alice" in result
    assert "bob" in result
    assert "|" in result


def test_handle_text_tolerates_non_utf8_bytes(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.txt"
    legacy.write_bytes("caf\u00e9 cr\u00e8me".encode("latin-1"))

    result = input_handler.handle_text(legacy)

    assert "caf\u00e9 cr\u00e8me" in result


def test_handle_text_strips_utf8_bom(tmp_path: Path) -> None:
    bom_file = tmp_path / "bom.txt"
    bom_file.write_bytes(b"\xef\xbb\xbfhello")

    result = input_handler.handle_text(bom_file)

    assert "hello" in result
    assert "\ufeff" not in result


def test_handle_html_converts_structure(tmp_path: Path) -> None:
    html = (
        "<html><head><title>Ignored</title><style>body { color: red; }</style></head><body>"
        "<h1>Title</h1>"
        '<p>Hello <strong>world</strong>, see <a href="https://example.com">the docs</a>.</p>'
        "<ul><li>one</li><li>two</li></ul>"
        "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<script>alert('xss')</script>"
        "</body></html>"
    )
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")

    result = input_handler.handle_html(page)

    assert "# Title" in result
    assert "**world**" in result
    assert "[the docs](https://example.com)" in result
    assert "- one" in result
    assert "- two" in result
    assert "| a | b |" in result
    assert "| 1 | 2 |" in result
    # Script and style content must be stripped entirely.
    assert "alert" not in result
    assert "color: red" not in result
    assert "Ignored" not in result


def test_handle_html_ordered_list_and_code(tmp_path: Path) -> None:
    html = "<ol><li>first</li><li>second</li></ol><p>Use <code>pip</code>.</p>"
    page = tmp_path / "snippet.htm"
    page.write_text(html, encoding="utf-8")

    result = input_handler.handle_html(page)

    assert "1. first" in result
    assert "2. second" in result
    assert "`pip`" in result
