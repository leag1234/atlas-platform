"""Figures of a built file, counted on the file itself.

What an answer may state about a file it produced (pages, slides, sheets,
bullets, words, pictures, boxes to tick) is read here, from the file's bytes,
with the readers the checks already use (verify.printed_pictures,
readback.read). Nothing is estimated: a figure that cannot be measured is left
out of the result.
"""

from __future__ import annotations

import io
import re

from .cells import BOXES

UNITS = ("pages", "slides", "sheets", "bullets", "words", "pictures", "checkboxes")
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")


def words(text: str) -> int:
    """The words of a text, a hyphenated or elided word counted once."""
    return len(_WORD.findall(text))


def boxes(lines: list[str]) -> int:
    """The lines that start with a box to tick."""
    return sum(1 for line in lines if line.lstrip().startswith(BOXES))


def measure(raw: bytes, suffix: str, pages: int | None = None) -> dict[str, int]:
    """The figures of a file. pages: the rendered pages when they were
    measured elsewhere (a Word file's pages exist only once printed)."""
    from .verify import printed_pictures

    found: dict[str, int] = {}
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            found["pages"] = len(reader.pages)
            lines = [
                line
                for page in reader.pages
                for line in (page.extract_text() or "").splitlines()
            ]
            found["words"] = words(" ".join(lines))
            found["checkboxes"] = boxes(lines)
        elif suffix == ".docx":
            from .readback import read

            document = read(raw, suffix)
            blocks = [
                line for section in document.sections for line in section.blocks()
            ]
            found["words"] = words(" ".join([document.title, *blocks]))
            found["bullets"] = sum(len(s.bullets) for s in document.sections)
            found["checkboxes"] = boxes(blocks)
            if pages:
                found["pages"] = pages
        elif suffix == ".pptx":
            from pptx import Presentation

            deck = Presentation(io.BytesIO(raw))
            lines = [
                paragraph.text
                for slide in deck.slides
                for shape in slide.shapes
                if shape.has_text_frame
                for paragraph in shape.text_frame.paragraphs
            ]
            found["slides"] = len(deck.slides)
            found["words"] = words(" ".join(lines))
            found["checkboxes"] = boxes(lines)
            from .readback import read

            found["bullets"] = sum(len(s.bullets) for s in read(raw, suffix).sections)
        elif suffix == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(raw), read_only=True)
            try:
                found["sheets"] = len(workbook.worksheets)
                cells = [
                    str(value)
                    for sheet in workbook.worksheets
                    for row in sheet.iter_rows(values_only=True)
                    for value in row
                    if isinstance(value, str)
                ]
            finally:
                workbook.close()
            found["words"] = words(" ".join(cells))
            found["checkboxes"] = boxes(cells)
    except Exception:  # noqa: BLE001 - an unreadable file gives no figure
        return {}
    shown = printed_pictures(raw, suffix)
    if shown is not None:
        found["pictures"] = shown
    return found
