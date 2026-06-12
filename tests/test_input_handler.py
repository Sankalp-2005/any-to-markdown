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


def test_handle_csv_produces_markdown_table(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("name,age\nalice,30\nbob,25\n", encoding="utf-8")

    result = input_handler.handle_csv(csv_path)

    assert "alice" in result
    assert "bob" in result
    assert "|" in result
