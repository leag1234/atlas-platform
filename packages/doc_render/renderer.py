"""Offline builders; no templates containing user-visible boilerplate."""

from collections.abc import Callable
from datetime import datetime, timezone
from io import BytesIO
from functools import partial
from pathlib import Path
from typing import Any
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Inches as DocInches
from docx.shared import Pt as DocPt
from docx.enum.text import WD_BREAK
from docx.text.paragraph import Paragraph
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from . import emoji
from .cells import (
    PAGE_COLUMNS,
    SHEET_COLUMNS,
    BOX,
    Reading,
    Span,
    checkbox,
    prose,
    shown,
    whole_link,
)
from .glyphs import adapt
from .layout import (
    Geometry,
    report_geometry,
    FONT,
    FOOTER_BASELINE,
    FOOTER_PT,
    SLIDE,
    image_path,
    kept_with_next,
    pages,
    table_type,
)
from .schema import Content
from .spacing import bound
from .verify import Verification, verify

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def normalized_package(raw: bytes) -> bytes:
    """An OOXML package with fixed timestamps and metadata, nested ones included.

    A chart embeds the workbook of its numbers (ppt/embeddings/*.xlsx), whose
    own properties carry the time of writing: a deck with a chart changed
    bytes on every build (2026-11-21).
    """
    output = BytesIO()
    with ZipFile(BytesIO(raw)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            data = source.read(name)
            if name == "docProps/core.xml":
                # Normalize library-generated timestamps and author/template metadata.
                from lxml import etree

                root = etree.fromstring(data)
                for element in root:
                    local = etree.QName(element).localname
                    if local in {"created", "modified"}:
                        element.text = "2000-01-01T00:00:00Z"
                    elif local not in {"title"}:
                        element.text = ""
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            elif name.endswith((".xlsx", ".docx", ".pptx")) and data[:2] == b"PK":
                data = normalized_package(data)
            info = ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, data)
    return output.getvalue()


def normalize_zip(path: Path) -> None:
    path.write_bytes(normalized_package(path.read_bytes()))


def build_slides(content: Content, path: Path, root: Path) -> None:
    """A themed deck (deck.py): layouts follow from what each slide holds."""
    from .deck import build_deck

    build_deck(content, path, root)


def draw_line(canvas: Canvas, x: float, y: float, line: str, size: float) -> None:
    """A line at its baseline: text in the face, each emoji as its picture.

    The picture has the emoji font's own ascent and descent around the
    baseline, at the size of the text (emoji.py).
    """
    run = ""
    for token in [*emoji.tokens(line), ""]:
        drawn = emoji.picture(token) if token and emoji.is_emoji(token) else None
        if drawn is None and token:
            run += token
            continue
        if run:
            canvas.drawString(x, y, run)
            x += pdfmetrics.stringWidth(run, FONT, size)
            run = ""
        if drawn is not None:
            canvas.drawImage(
                ImageReader(BytesIO(drawn.png)),
                x,
                y - (drawn.height - drawn.ascent) * size,
                width=drawn.advance * size,
                height=drawn.height * size,
                mask="auto",
            )
            x += drawn.advance * size


def build_pdf(
    content: Content, path: Path, root: Path, geometry: Geometry | None = None
) -> None:
    """A report on A4 pages; a presentation passes the slide geometry."""
    g = geometry or report_geometry(content)
    layout = pages(content, root, geometry=g)
    canvas = Canvas(
        str(path),
        pagesize=(g.width, g.height),
        invariant=1,
        pageCompression=1,
        bottomup=1,
    )
    canvas.setTitle(shown(content.title))
    canvas.setAuthor("")
    for page in layout:
        for placed in page:
            block, top = placed.block, placed.top
            if block.image:
                canvas.drawImage(
                    str(block.image),
                    g.margin,
                    g.height - top - block.height + 8,
                    width=block.width,
                    height=block.height - 8,
                    mask="auto",
                )
                continue
            canvas.setFont(FONT, block.size)
            if block.check:
                # A box to tick, ticked when written "[x]".
                side = block.size * 0.75
                left, bottom = g.margin + 1, g.height - top - block.size - 2
                canvas.setLineWidth(0.8)
                canvas.rect(left, bottom, side, side, fill=0, stroke=1)
                if block.check != BOX:
                    canvas.line(
                        left + 2, bottom + side / 2, left + side / 2.5, bottom + 2
                    )
                    canvas.line(
                        left + side / 2.5,
                        bottom + 2,
                        left + side - 1.5,
                        bottom + side - 1.5,
                    )
            elif block.bullet:
                canvas.circle(g.margin + 5, g.height - top - 8, 2, fill=1, stroke=0)
            for col, lines in enumerate(block.lines):
                cell_width = block.width / len(block.lines)
                x = g.margin + col * cell_width
                if block.table:
                    canvas.rect(
                        x, g.height - top - block.height, cell_width, block.height
                    )
                x += block.pad if block.table else (16 if block.bullet else 0)
                for line_index, line in enumerate(lines):
                    leading = block.leading if block.table else block.size * 1.35
                    draw_line(
                        canvas,
                        x,
                        g.height - top - block.size - 3 - line_index * leading,
                        line,
                        block.size,
                    )
        if content.footer:
            # The running footer, at the foot of every page (Content.footer).
            canvas.setFont(FONT, FOOTER_PT)
            draw_line(
                canvas,
                g.margin,
                FOOTER_BASELINE,
                adapt(shown(content.footer)),
                FOOTER_PT,
            )
        canvas.showPage()
    canvas.save()


def write_runs(paragraph: Paragraph, text: str, *, weight: bool = True) -> None:
    """A text as runs: its emphasis is read, its markers are never written.

    A heading keeps the weight of its style ("## **Summary**" is a habit of
    writing, not a request for another weight) and takes italics only.
    """
    for span in prose(text):
        # Never a line break inside a number or between it and its unit.
        run = paragraph.add_run(bound(span.text))
        if span.bold and weight:
            run.bold = True
        if span.italic:
            run.italic = True


# Report spacing (build_document), in points and inches.
HEADING_SPACE_BEFORE = 12
TITLE_SPACE_AFTER = 8
SIDE_MARGIN_INCHES = 1.0
# The compact report (compact=1 to 4): what a file printed just over the
# pages the person asked takes, by code, before its text is cut; each level
# adds to the one before, inside the house style (staging -162 to -170):
# 1 the spacing around headings and the title, 2 the page margins, 3 the
# tables (cell padding, 9 pt cells), 4 the body type down to 10 pt, the
# readable minimum. Headings and the title keep their size.
COMPACT_HEADING_SPACE = (6, 2)
COMPACT_TITLE_SPACE_AFTER = 4
COMPACT_MARGIN_INCHES = 0.75
COMPACT_TABLE_PT = 9
COMPACT_CELL_TWIPS = 60
COMPACT_BODY_PT = 10
COMPACT_LEVELS = 4


def unnumbered(paragraph: Paragraph) -> None:
    """A list paragraph shown without its bullet (w:numId 0 ends numbering)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = paragraph._p.get_or_add_pPr()
    numbering = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    number = OxmlElement("w:numId")
    number.set(qn("w:val"), "0")
    numbering.append(level)
    numbering.append(number)
    style = properties.find(qn("w:pStyle"))
    if style is None:
        properties.insert(0, numbering)
    else:
        style.addnext(numbering)


def compact_level(document: Any) -> int:
    """The compact level a built DOCX (python-docx Document) was laid out at."""
    normal = document.styles["Normal"].font.size
    if normal is not None and normal.pt == COMPACT_BODY_PT:
        return 4
    table = document.styles["Table Grid"].font.size
    if table is not None and table.pt == COMPACT_TABLE_PT:
        return 3
    margin = document.sections[0].left_margin
    if margin is not None and abs(margin.inches - COMPACT_MARGIN_INCHES) < 0.01:
        return 2
    before = document.styles["Heading 1"].paragraph_format.space_before
    if before is not None and before.pt == COMPACT_HEADING_SPACE[0]:
        return 1
    return 0


def _cell_padding(table: Any, twips: int) -> None:
    """Narrower left and right cell padding for a whole table."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table._tbl.tblPr
    margins = OxmlElement("w:tblCellMar")
    for side in ("left", "right"):
        element = OxmlElement(f"w:{side}")
        element.set(qn("w:w"), str(twips))
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    properties.append(margins)


def build_document(
    content: Content, path: Path, root: Path, *, compact: int = 0
) -> None:
    """A Word report whose sections follow each other on the page.

    A page break used to precede every heading: a six-section report of a
    few lines each took six pages filled to a twentieth (staging -150,
    2026-11-21). The breaks are now those of the PDF layout (layout.pages):
    a title page when the content has a cover, a section the person or the
    plan starts on a new page, and a very long section. A break that was asked
    for (cover, Section.page_break) is a page break of its own paragraph; a
    long section's heading starts its page with "page break before", which
    readback.py tells apart: only the first is written back as asked.
    Headings keep with the text that follows them (no heading at a page
    foot), a lead-in sentence with the list or table it introduces, and the
    points of a short list with each other (layout.kept_with_next).
    """
    breaks: dict[int, int] = {}
    pages(content, root, geometry=report_geometry(content), breaks=breaks)
    document = Document()
    document.core_properties.title = shown(content.title)
    wide = any(
        len(table.headers) > 8
        for section in content.sections
        for table in section.tables
    )
    # The house spacing of a report: the template's 24 pt above each heading,
    # 15 pt under the title and 1.25 inch side margins made a one-page report
    # of 221 words print on two pages (staging -158, LibreOffice). The type
    # sizes stay those of the template.
    for style in ("Heading 1", "Heading 2"):
        document.styles[style].paragraph_format.space_before = DocPt(
            HEADING_SPACE_BEFORE
        )
    document.styles["Title"].paragraph_format.space_after = DocPt(TITLE_SPACE_AFTER)
    first = document.sections[0]
    first.left_margin = first.right_margin = DocInches(SIDE_MARGIN_INCHES)
    compact = max(0, min(COMPACT_LEVELS, int(compact)))
    if compact >= 1:
        for style in ("Heading 1", "Heading 2"):
            heading = document.styles[style].paragraph_format
            heading.space_before = DocPt(COMPACT_HEADING_SPACE[0])
            heading.space_after = DocPt(COMPACT_HEADING_SPACE[1])
        document.styles["Title"].paragraph_format.space_after = DocPt(
            COMPACT_TITLE_SPACE_AFTER
        )
    if compact >= 2:
        first.left_margin = first.right_margin = DocInches(COMPACT_MARGIN_INCHES)
        first.top_margin = first.bottom_margin = DocInches(COMPACT_MARGIN_INCHES)
    if compact >= 3:
        document.styles["Table Grid"].font.size = DocPt(COMPACT_TABLE_PT)
    if compact >= 4:
        document.styles["Normal"].font.size = DocPt(COMPACT_BODY_PT)
    if wide:
        # A table of more than eight columns gets the long side of the page
        # and narrow side margins.
        page = document.sections[0]
        page.orientation = WD_ORIENT.LANDSCAPE
        page.page_width, page.page_height = page.page_height, page.page_width
        page.left_margin = page.right_margin = DocInches(0.5)
    write_runs(document.add_heading("", 0), content.title, weight=False)
    for index, section in enumerate(content.sections):
        asked = (section.page_break if index else content.cover) and index in breaks
        if asked:
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        if section.drawn() and (index or not content.repeats_title()):
            heading = document.add_heading("", section.level)
            heading.paragraph_format.keep_with_next = True
            if index in breaks and not asked:
                heading.paragraph_format.page_break_before = True
            write_runs(heading, section.heading, weight=False)
        # A lead-in stays with what it introduces, a short list together
        # (layout.kept_with_next, the rule of the PDF layout).
        keep = iter(kept_with_next(section))
        for text in section.paragraphs:
            paragraph = document.add_paragraph()
            write_runs(paragraph, text)
            paragraph.paragraph_format.keep_with_next = next(keep) or None
            if emoji.pictures_only(shown(text)):
                for run in paragraph.runs:
                    run.font.size = DocPt(emoji.PICTURE_SIZE)
        for text in section.bullets:
            point = document.add_paragraph(style="List Bullet")
            write_runs(point, text)
            point.paragraph_format.keep_with_next = next(keep) or None
            if checkbox(shown(text))[0]:
                # A box to tick replaces the bullet: the list style without
                # its numbering (numId 0), the box written as its character.
                unnumbered(point)
        for table in section.tables:
            grid = document.add_table(rows=1, cols=len(table.headers))
            grid.style = "Table Grid"
            size = table_type(len(table.headers))[0]
            small = size < 11
            for position, row in enumerate(table.read()):
                cells = grid.rows[0].cells if position == 0 else grid.add_row().cells
                for cell, reading in zip(cells, row, strict=True):
                    if not small and reading.spans in ((), (Span(reading.text),)):
                        cell.text = bound(reading.text)
                        continue
                    for span in reading.spans:
                        run = cell.paragraphs[0].add_run(bound(span.text))
                        run.bold = span.bold or None
                        run.italic = span.italic or None
                        if small:
                            run.font.size = DocPt(size)
        for value in section.images:
            document.add_picture(str(image_path(root, value)), width=DocInches(4))
    if content.footer:
        # The running footer of every page (Content.footer): the page footer
        # of the document, never a paragraph of the body.
        footer = document.sections[0].footer
        footer.is_linked_to_previous = False
        write_runs(footer.paragraphs[0], content.footer)
    if compact >= 3:
        for grid in document.tables:
            _cell_padding(grid, COMPACT_CELL_TWIPS)
            for row in grid.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            if (
                                run.font.size is None
                                or run.font.size.pt > COMPACT_TABLE_PT
                            ):
                                run.font.size = DocPt(COMPACT_TABLE_PT)
    document.save(str(path))


def build_workbook(content: Content, path: Path, root: Path) -> None:
    from .spreadsheet import table_areas

    # Where each sheet's table is, checked before anything is written.
    areas = table_areas(content)
    workbook = Workbook()
    workbook.remove(workbook.worksheets[0])
    workbook.properties.title = shown(content.title)
    workbook.properties.created = workbook.properties.modified = EPOCH
    # The written area of each sheet, empty table cells included: a formula may
    # reference an empty cell of the table, never a cell outside what was written.
    extents: dict[str, tuple[int, int]] = {}
    for index, section in enumerate(content.sections):
        # Numeric tabs are structural identifiers, never invented document content.
        sheet = workbook.create_sheet(str(index + 1))
        texts = [
            *((section.heading,) if section.drawn() else ()),
            *section.paragraphs,
            *section.bullets,
        ]
        if index == 0 and not content.repeats_title():
            texts.insert(0, content.title)
        # The first table starts at cell A1: header in row 1, first data row
        # in row 2. A reference then means the same cell to the author of the
        # table, in the file and to the person who opens it. Texts written
        # above it shifted every formula (a total summed the heading, the
        # header and half of the rows, staging 2026-11-12). The title, the
        # heading and the texts follow the table after one empty row.
        rows: list[tuple[Reading, ...]] = []
        formula_rows: set[int] = set()
        if section.tables:
            first = section.tables[0]
            # A cell of this table that starts with "=" is a formula, whether
            # or not the table was flagged (see Table.holds_formulas).
            formula_rows.update(range(2, 2 + len(first.rows)))
            rows.extend(first.read())
            rows.append(())
        # A text is read as a cell is: its markers are never shown, and the
        # cell is bold or italic when the whole text is. It is never a number
        # nor a formula.
        rows.extend((Reading(prose(text), link=whole_link(text)),) for text in texts)
        for table in section.tables[1:]:
            # No formula here: table_areas refused it.
            rows.extend(table.read())
        extents[sheet.title] = (len(rows), max(map(len, rows)))
        typed: dict[str, tuple[float, str]] = {}
        computed: dict[str, str] = {}
        # Up to eight columns keep one comfortable width; a wider sheet fits
        # each column to what it holds, so twelve months stay in view.
        widths: dict[int, int] = {}
        fitted = extents[sheet.title][1] > 8
        for row_index, row in enumerate(rows, 1):
            for column, reading in enumerate(row, 1):
                fit = min(32, max(10, len(reading.text) + 4)) if fitted else 32
                widths[column] = max(widths.get(column, 0), fit)
                if not reading.text:
                    # An empty table cell is a truly empty cell: no value, no
                    # empty string. Formulas read it as Excel does.
                    continue
                cell = sheet.cell(row_index, column, reading.text)
                cell.data_type = "s"
                if reading.quoted:
                    # Typed "'=A1": the text "=A1", marked as Excel marks a
                    # cell entered with an apostrophe.
                    cell.quotePrefix = True
                elif row_index in formula_rows and reading.text.startswith("="):
                    cell.data_type = "f"
                    computed[cell.coordinate] = reading.text
                elif reading.number is not None:
                    # A cell typed as a number is one in every table, shown as
                    # typed: a sum never skips "12.50" or "1 250,00 €".
                    cell.value = reading.number
                    cell.number_format = reading.number_format
                    typed[cell.coordinate] = (reading.number, reading.number_format)
                elif reading.link:
                    cell.hyperlink = reading.link
                cell.font = Font(
                    name="Bitstream Vera Sans",
                    size=11,
                    bold=reading.bold or None,
                    italic=reading.italic or None,
                )
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for column, width in widths.items():
            sheet.column_dimensions[get_column_letter(column)].width = width
        if computed:
            from .spreadsheet import total_formats

            for coordinate, display in total_formats(typed, computed).items():
                sheet[coordinate].number_format = display
        image_row = len(rows) + 2
        for value in section.images:
            image = SheetImage(str(image_path(root, value)))
            scale = min(400 / image.width, 180 / image.height)
            image.width = int(image.width * scale)
            image.height = int(image.height * scale)
            sheet.add_image(image, f"A{image_row}")
            image_row += 15
    workbook.save(path)
    workbook.close()
    if any(
        table.holds_formulas()
        for section in content.sections
        for table in section.tables
    ):
        from .formulas import cache_formulas

        cache_formulas(path, extents, areas)


def render(
    content: Content,
    path: Path,
    *,
    image_root: Path,
    slides: bool = False,
    compact: int = 0,
) -> Verification:
    """Build and verify atomically; destination must be chosen by trusted code.

    With slides, a PDF has one slide-size page per slide, as the PPTX deck.
    With compact (a level, 1 to 4), a DOCX takes the compact report layout
    (build_document).
    """
    content = Content.model_validate(content.model_dump())
    if path.suffix != ".pptx":
        # Speaker notes, key numbers and charts are text and tables on pages.
        content = content.flattened()
    if path.suffix == ".xlsx":
        from .spreadsheet import bind_summaries

        content = bind_summaries(content)
    limit = SHEET_COLUMNS if path.suffix == ".xlsx" else PAGE_COLUMNS
    if any(
        len(table.headers) > limit
        for section in content.sections
        for table in section.tables
    ):
        raise ValueError(f"table_columns_limit:{limit}")
    # Apply identical font and image bounds for every output format; table rows
    # are laid out on pages for the formats that have pages.
    pages(
        content,
        image_root,
        tables=path.suffix != ".xlsx",
        geometry=report_geometry(content)
        if path.suffix == ".pdf" and not slides
        else SLIDE,
    )
    builders: dict[str, Callable[[Content, Path, Path], None]] = {
        ".pptx": build_slides,
        ".pdf": partial(build_pdf, geometry=SLIDE if slides else None),
        ".docx": partial(build_document, compact=compact),
        ".xlsx": build_workbook,
    }
    if path.suffix not in builders:
        raise ValueError("unsupported_output_format")
    with TemporaryDirectory(prefix="doc-render-", dir=path.parent) as temporary:
        staged = Path(temporary) / path.name
        builders[path.suffix](content, staged, image_root)
        if path.suffix != ".pdf":
            normalize_zip(staged)
        result = verify(
            staged,
            expected=content,
            report_root=image_root
            if path.suffix == ".docx" or (path.suffix == ".pdf" and not slides)
            else None,
        )
        staged.replace(path)
    return result
