"""Reopen files and distinguish physical pagination from package checks."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from pptx.shapes.autoshape import Shape
from pptx.shapes.graphfrm import GraphicFrame
from pypdf import PdfReader

from . import emoji
from .cells import shown
from .schema import Content
from .titles import slug_like
from .spacing import unbound
from .wordbreaks import overflowing, page_lines, split_words


class VerificationError(ValueError):
    """An artifact cannot be published under the requested verification policy."""


@dataclass(frozen=True)
class Verification:
    unit: Literal["page", "slide", "document", "worksheet"]
    count: int
    text: tuple[str, ...]
    pagination_verified: bool
    # Words cut in two at a line end (wordbreaks.py): a defect the renderer
    # avoids, reported rather than refused (a long address in a narrow table
    # cell has nowhere else to go).
    broken_words: tuple[str, ...] = ()
    # Defects of the form of a report's pages (form.py): nearly empty pages,
    # more pages than the content needs, a heading at a page foot. Reported,
    # not refused: the deliverable check refuses a file once with them.
    form: tuple[str, ...] = ()


def verify(
    path: Path,
    *,
    expected: Content | None = None,
    expected_count: int | None = None,
    require_pagination: bool = False,
    report_root: Path | None = None,
) -> Verification:
    """Parsing errors propagate; blank units and missing content fail explicitly.

    With report_root (the picture folder of a report: a DOCX or a PDF laid
    out on A4 pages, not slides), the form of its pages is measured on the
    rendered PDF (form.py): the file itself for a PDF, the PDF of the same
    content for a DOCX.

    A title that is a file name ("presentation-hirondelle") fails too
    (titles.py): no deliverable shows a slug as its title.
    """
    if expected is not None and slug_like(expected.title):
        raise VerificationError("file_name_title")
    text: list[str] = []
    broken: list[str] = []
    pictured: list[bool] = []
    unit: Literal["page", "slide", "document", "worksheet"]
    if path.suffix == ".pdf":
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise VerificationError("encrypted_pdf")
        text = [page.extract_text() for page in reader.pages]
        # A page that holds only a picture (a picture moved to the next page
        # with no text after it) is not blank.
        pictured = [
            not value.strip() and bool(page.images)
            for value, page in zip(text, reader.pages)
        ]
        unit = "page"
    elif path.suffix == ".pptx":
        deck = Presentation(str(path))
        for slide in deck.slides:
            parts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    parts.append(cast(Shape, shape).text)
                if shape.has_table:
                    parts.extend(
                        cell.text
                        for row in cast(GraphicFrame, shape).table.rows
                        for cell in row.cells
                    )
                if getattr(shape, "has_chart", False):
                    # A chart's words are its categories (its numbers are data).
                    parts.extend(
                        str(label)
                        for label in cast(GraphicFrame, shape).chart.plots[0].categories
                    )
            if slide.has_notes_slide:
                parts.append(slide.notes_slide.notes_text_frame.text)
            text.append("\n".join(parts))
        broken = overflowing(deck)
        unit = "slide"
    elif path.suffix == ".docx":
        document = Document(str(path))
        parts = [paragraph.text for paragraph in document.paragraphs]
        parts.extend(
            cell.text
            for table in document.tables
            for row in table.rows
            for cell in row.cells
        )
        text = ["\n".join(parts)]
        broken = unbound(text[0])
        unit = "document"
    elif path.suffix == ".xlsx":
        workbook = load_workbook(path, data_only=False, read_only=True)
        try:
            text = [
                "\n".join(
                    str(cell.value)
                    for row in sheet.iter_rows()
                    for cell in row
                    if cell.value is not None
                )
                for sheet in workbook.worksheets
            ]
        finally:
            workbook.close()
        unit = "worksheet"
    else:
        raise VerificationError("unsupported_output_format")
    if not text or any(
        not value.strip() and not (path.suffix == ".pdf" and pictured[index])
        for index, value in enumerate(text)
    ):
        raise VerificationError("empty_document_or_unit")
    if expected_count is not None and len(text) != expected_count:
        raise VerificationError(f"unit_count_mismatch:{len(text)}:{expected_count}")
    if expected and path.suffix == ".pdf":
        source = "\n".join(expected.texts())
        broken = [
            word for value in text for word in split_words(page_lines(value), source)
        ]
    paginated = unit in {"page", "slide"}
    if require_pagination and not paginated:
        raise VerificationError("office_layout_engine_required")
    if expected and path.suffix == ".xlsx":
        from .spreadsheet import bind_summaries

        expected = bind_summaries(expected)
    if expected:
        # A PDF page draws its emoji as pictures (emoji.py): its text layer
        # holds the rest of each text.
        drawn = emoji.without_pictures if path.suffix == ".pdf" else lambda value: value
        units = ["".join(value.split()) for value in text]
        if paginated:
            seen: set[str] = set()
            headings = sorted(
                {
                    "".join(drawn(shown(section.heading)).split())
                    for section in expected.sections
                },
                key=len,
                reverse=True,
            )
            for index, value in enumerate(units):
                for heading in headings:
                    if value.startswith(heading):
                        if heading in seen:
                            units[index] = value[len(heading) :]
                        break
                seen.update(heading for heading in headings if heading in value)
        combined = "".join(units)
        # A workbook stores a number for a cell typed as one.
        wanted = [
            drawn(value) for value in expected.texts(sheet=path.suffix == ".xlsx")
        ]
        if any("".join(value.split()) not in combined for value in wanted):
            raise VerificationError("missing_expected_text")
    found: tuple[str, ...] = ()
    if (
        report_root is not None
        and expected is not None
        and path.suffix
        in {
            ".pdf",
            ".docx",
        }
    ):
        from .form import check

        found = tuple(
            check(
                expected,
                report_root,
                path.read_bytes() if path.suffix == ".pdf" else None,
            )
        )
    return Verification(unit, len(text), tuple(text), paginated, tuple(broken), found)
