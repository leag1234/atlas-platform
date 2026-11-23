"""Measured layout shared by the PDF writer and slide builder."""

from dataclasses import dataclass
from pathlib import Path
import re

import reportlab
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from . import emoji
from .cells import shown
from .glyphs import drawable
from .schema import Content, Section
from .spacing import bound

FONT = "AtlasContentVera"
WIDTH, HEIGHT, MARGIN = 720.0, 405.0, 30.0
BODY_WIDTH = WIDTH - 2 * MARGIN


@dataclass(frozen=True)
class Geometry:
    """The page a format lays content on.

    A slide starts each section on a new slide and repeats its heading when it
    continues. A PDF report is an A4 portrait page where sections follow each
    other: on 16:9 slide-size pages, one section per page, a short report took
    16 pages filled to a quarter (staging 2026-11-12).
    """

    width: float
    height: float
    margin: float = MARGIN
    flow: bool = False
    image_height: float = 180.0

    @property
    def body(self) -> float:
        return self.width - 2 * self.margin


SLIDE = Geometry(WIDTH, HEIGHT)
A4 = Geometry(595.28, 841.89, 50.0, flow=True, image_height=320.0)
# A table of more than eight columns (twelve months and a total) needs the width.
A4_LANDSCAPE = Geometry(841.89, 595.28, 40.0, flow=True, image_height=260.0)
# A section at least this many pages long opens a page (long_section).
LONG_SECTION = 1.5
# A list of at most this many points stays on one page (kept_with_next).
SHORT_LIST = 6
# Type sizes of a section heading and of a sub-heading (Section.level 2).
HEADING_PT = 19.0
SUBHEADING_PT = 15.0
# The opening of a section's text that a heading never leaves behind.
OPENING_PT = 2 * 16 + 8.0
# The space above a section's heading on a report page.
SECTION_GAP = 10.0


def lead_in(text: str) -> bool:
    """A sentence that introduces what follows it ("we recommend:")."""
    return shown(text).rstrip().endswith((":", "\uff1a"))


def kept_with_next(section: object) -> tuple[bool, ...]:
    """For each paragraph then each bullet of a section: whether it stays on
    the page of the block that follows it (Word's "keep with next").

    Staging -151 (2026-11-21): a one-page report printed "Recommendations"
    and "Based on the trial results, we recommend:" at the foot of page 1 and
    all four points on page 2; another version left the first point of a
    list alone at a page foot. The heading already kept with its text. Now
    a lead-in sentence (ending with a colon) keeps with what it introduces,
    a list of up to SHORT_LIST points stays together, and a longer one never
    leaves its first or its last point alone on a page. The DOCX writer
    (renderer.build_document) and the PDF layout (pages) apply the same
    rule, so the form check of a DOCX (form.py, on the PDF of the same
    content) sees the breaks Word and LibreOffice make.
    """
    from .schema import Section

    if not isinstance(section, Section):
        return ()
    after = bool(section.bullets or section.tables or section.images)
    count = len(section.paragraphs)
    flags = [
        lead_in(text) and (index < count - 1 or after)
        for index, text in enumerate(section.paragraphs)
    ]
    points = len(section.bullets)
    for index in range(points):
        last = index == points - 1
        flags.append(
            not last and (points <= SHORT_LIST or index == 0 or index == points - 2)
        )
    return tuple(flags)


def report_geometry(content: Content) -> Geometry:
    """The page of a PDF report: A4 portrait, landscape for a wide table."""
    wide = any(
        len(table.headers) > 8
        for section in content.sections
        for table in section.tables
    )
    return A4_LANDSCAPE if wide else A4


@dataclass(frozen=True)
class Block:
    lines: tuple[tuple[str, ...], ...] = ()
    size: float = 12
    image: Path | None = None
    width: float = BODY_WIDTH
    height: float = 0
    table: bool = False
    bullet: bool = False
    # Table rows only: line spacing and the horizontal padding of a cell.
    leading: float = 15
    pad: float = 6


@dataclass(frozen=True)
class Placed:
    block: Block
    top: float


def chain(body: list[Block], keep: list[bool], start: int) -> float:
    """The height from body[start] to the opening lines of the block its
    kept run ends on (a table row or a picture whole)."""
    height = 0.0
    for position in range(start, len(body)):
        block = body[position]
        if keep[position]:
            height += block.height
            continue
        if block.table or block.image is not None:
            return height + block.height
        lines = min(len(block.lines[0]), 2)
        return height + min(block.height, lines * block.size * 1.35 + 8)
    return height


def font() -> TTFont:
    path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    registered = TTFont(FONT, str(path))
    pdfmetrics.registerFont(registered)
    return registered


def width_of(text: str, size: float) -> float:
    """The drawn width of a line: its text in the face, its emoji as pictures."""
    total, run = 0.0, ""
    for token in emoji.tokens(text):
        if emoji.is_emoji(token) and emoji.drawable(token):
            total += (
                pdfmetrics.stringWidth(run, FONT, size) + emoji.advance(token) * size
            )
            run = ""
        else:
            run += token
    return total + pdfmetrics.stringWidth(run, FONT, size)


def _cut(word: str, width: float, size: float) -> list[str]:
    """A word longer than the line, cut where it must (an address, a code);
    an emoji sequence (a family joined by U+200D) is never cut."""
    parts: list[str] = []
    current = ""
    for character in emoji.tokens(word):
        if width_of(current + character, size) > width:
            if not current:
                raise ValueError("glyph_exceeds_line_width")
            parts.append(current)
            current = ""
        current += character
    if current:
        parts.append(current)
    return parts


def wrap(text: str, width: float, size: float) -> tuple[str, ...]:
    """Lines broken between words; a word wider than the line is cut.

    Lines used to break at any character: headings and table cells showed
    "WORKSHO / P" and "de la ma / ison" (staging 2026-11-16). The space at a
    break is dropped; other whitespace is kept as supplied. A number and
    its unit are bound (spacing.py): "3 000" and "20 %" never break.
    """
    text = bound(text)
    lines: list[str] = []
    current = ""
    space = ""
    for piece in re.findall(r"[^\S\u00a0]+|(?:\S|\u00a0)+", text):
        if piece.isspace():
            space = piece if current or lines else ""
            if not current and not lines:
                current = piece  # leading whitespace is kept as supplied
                space = ""
            continue
        candidate = current + space + piece
        space = ""
        if width_of(candidate, size) <= width:
            current = candidate
            continue
        if current.strip():
            lines.append(current)
        current = ""
        if width_of(piece, size) <= width:
            current = piece
            continue
        parts = _cut(piece, width, size)
        lines.extend(parts[:-1])
        current = parts[-1]
    if space and current and width_of(current + space, size) <= width:
        current += space
    if current:
        lines.append(current)
    return tuple(lines)


def table_type(columns: int) -> tuple[float, float, float, float]:
    """Font size, line spacing, side padding and row padding of a table.

    More than eight columns are set smaller and tighter: a label, twelve
    months and a total hold "1 250,00 €" on one line each.
    """
    if columns <= 8:
        return 11.0, 15.0, 6.0, 12.0
    if columns <= 11:
        return 9.0, 12.0, 3.0, 8.0
    return 7.0, 10.0, 2.0, 8.0


def text_block(
    text: str, size: float = 12, *, bullet: bool = False, body: float = BODY_WIDTH
) -> Block:
    lines = wrap(text, body - (16 if bullet else 0), size)
    return Block(
        lines=(lines,),
        size=size,
        height=len(lines) * size * 1.35 + 8,
        bullet=bullet,
        width=body,
    )


def heading_block(section: object, body: float) -> Block:
    """A section's heading as laid out; a continuation writes none."""
    if isinstance(section, Section) and section.continued:
        return Block(lines=((),), height=0.0, width=body)
    size = HEADING_PT if getattr(section, "level", 1) == 1 else SUBHEADING_PT
    return text_block(shown(getattr(section, "heading", "")), size, body=body)


def image_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("image_path_must_be_relative")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("image_outside_root")
    if path.stat().st_size > 10_000_000:
        raise ValueError("image_size_limit")
    with Image.open(path) as image:
        if (
            image.format not in {"PNG", "JPEG"}
            or image.width * image.height > 16_000_000
        ):
            raise ValueError("unsupported_image")
        image.verify()
    return path


def picture_opening(
    section: object, image_root: Path, g: Geometry, heading: float
) -> float:
    """Height of a section up to its first picture (0 without a picture or
    with a table, which keeps the former breaks)."""
    from .schema import Section

    if not isinstance(section, Section) or not section.images or section.tables:
        return 0.0
    height = heading
    for text in section.paragraphs:
        plain = shown(text)
        size = emoji.PICTURE_SIZE if emoji.pictures_only(plain) else 12
        height += text_block(plain, size, body=g.body).height
    for text in section.bullets:
        height += text_block(shown(text), bullet=True, body=g.body).height
    with Image.open(image_path(image_root, section.images[0])) as image:
        scale = min(g.body / image.width, g.image_height / image.height)
        return height + image.height * scale + 8


def long_section(blocks: list[Block], g: Geometry, top: float) -> bool:
    """Whether a very long section starts on a new page (reports only).

    The rule: a section whose heading and body fill at least LONG_SECTION
    pages by themselves starts on a new page when the page it would start on
    is already at least half full. It then opens a page as a chapter does,
    and the page it leaves stays at least half filled. Every other section
    follows the one before it on the same page.
    """
    body = g.height - 2 * g.margin
    room = g.height - g.margin - top
    return (
        g.flow
        and sum(b.height for b in blocks) >= LONG_SECTION * body
        and room < body / 2
    )


def pages(
    content: Content,
    image_root: Path,
    *,
    tables: bool = True,
    geometry: Geometry = SLIDE,
    breaks: dict[int, int] | None = None,
) -> tuple[tuple[Placed, ...], ...]:
    """Lay the content out on pages; a workbook has none for its tables.

    On a report page (a flowing geometry) a new page starts only where it
    must: the title page of a content with a cover (0 in breaks), a section
    the person or the plan starts on a new page (Section.page_break), a very
    long section (long_section), and the overflow of a full page. breaks, when
    given, receives the index of every section that starts a page by such a
    decision, not by overflow, with the index of that page: the DOCX writer
    places the same breaks (renderer.build_document) and lets Word flow the
    rest, and the form check (form.py) knows which pages end where a break
    was asked.
    """
    g = geometry
    font()
    for text in content.texts():
        if not all(drawable(token) for token in emoji.tokens(text)):
            raise ValueError("unsupported_font_character")
    result: list[tuple[Placed, ...]] = []
    current: list[Placed] = []
    top = g.margin
    page_body = g.height - 2 * g.margin

    def laid(section: Section) -> tuple[Block, list[Block], list[bool]]:
        """A section's heading, its blocks, and which keep with the next."""
        heading = heading_block(section, g.body)
        if heading.height > 95:
            raise ValueError("heading_exceeds_layout")
        body: list[Block] = []
        # Inline markers are read, never laid out: a page and a slide show
        # the text in one face.
        for text in section.paragraphs:
            plain = shown(text)
            size = emoji.PICTURE_SIZE if emoji.pictures_only(plain) else 12
            body.append(text_block(plain, size, body=g.body))
        for text in section.bullets:
            body.append(text_block(shown(text), bullet=True, body=g.body))
        for table in section.tables if tables else ():
            size, leading, pad, row_pad = table_type(len(table.headers))
            for row in table.read():
                lines = tuple(
                    wrap(cell.text, g.body / len(row) - 2 * pad, size) for cell in row
                )
                body.append(
                    Block(
                        lines=lines,
                        size=size,
                        table=True,
                        leading=leading,
                        pad=pad,
                        width=g.body,
                        # A row of empty cells keeps the height of one line.
                        height=max(1, *map(len, lines)) * leading + row_pad,
                    )
                )
        for value in section.images:
            path = image_path(image_root, value)
            with Image.open(path) as image:
                scale = min(g.body / image.width, g.image_height / image.height)
                body.append(
                    Block(
                        image=path,
                        width=image.width * scale,
                        height=image.height * scale + 8,
                    )
                )
        # What stays together on a report page (kept_with_next): a lead-in
        # and what it introduces, the points of a short list.
        keep = list(kept_with_next(section))[: len(body)] if g.flow else []
        keep += [False] * (len(body) - len(keep))
        return heading, body, keep

    parts = [laid(section) for section in content.sections]

    def need(index: int) -> float:
        """What a section needs under its heading's top: a heading never
        ends a page, it brings the opening of its text; a lead-in that opens
        the section, the start of its list; a heading over sub-sections, the
        first one's heading and opening."""
        heading, body, keep = parts[index]
        opening = OPENING_PT
        if body and keep[0] and heading.height + chain(body, keep, 0) <= page_body:
            opening = max(opening, chain(body, keep, 0))
        if not body and index + 1 < len(parts):
            opening = SECTION_GAP + need(index + 1)
        return heading.height + opening

    for index, section in enumerate(content.sections):
        heading, body, keep = parts[index]
        gap = 0.0 if section.continued else SECTION_GAP
        if index == 0:
            current, top = [], g.margin
            title = text_block(shown(content.title), 23, body=g.body)
            if title.height > 100:
                raise ValueError("title_exceeds_layout")
            current.append(Placed(title, top))
            top += title.height
            if g.flow and content.cover:
                # A title page: the title alone, the first section after it.
                result.append(tuple(current))
                current, top = [], g.margin
                if breaks is not None:
                    breaks[0] = len(result)
        elif not g.flow:
            # A slide per section.
            result.append(tuple(current))
            current, top = [], g.margin
        elif section.page_break or (
            not section.continued and long_section([heading, *body], g, top)
        ):
            result.append(tuple(current))
            current, top = [], g.margin
            if breaks is not None:
                breaks[index] = len(result)
        elif (
            g.height - g.margin - top - gap < need(index)
            or g.height - g.margin - top
            < picture_opening(section, image_root, g, heading.height)
            <= g.height - 2 * g.margin
        ):
            # On a report page, a heading never ends a page, and a section's
            # text before its first picture stays on the page of that picture
            # (a picture book put a word at the foot of a page and its picture
            # on the next, 2026-11-16).
            result.append(tuple(current))
            current, top = [], g.margin
        else:
            top += gap
        if section.drawn() and (index or not content.repeats_title()):
            # Under the title, a heading that repeats it is not written again.
            current.append(Placed(heading, top))
            top += heading.height
        # A continued slide repeats its heading; a continued report page does not.
        repeat = 0.0 if g.flow else heading.height
        # Split text across pages. Table rows/images remain atomic and fail if oversized.
        for position, block in enumerate(body):
            if (
                position
                and keep[position]
                and not keep[position - 1]
                and top > g.margin
                and g.height - g.margin - top < chain(body, keep, position) <= page_body
            ):
                # A lead-in and its list, or a short list, start together on
                # the next page rather than leave their opening at a page foot.
                result.append(tuple(current))
                current, top = [], g.margin
            pending = block
            while True:
                room = g.height - g.margin - top
                if pending.height <= room:
                    current.append(Placed(pending, top))
                    top += pending.height
                    break
                if not pending.table and pending.image is None:
                    count = int((room - 8) / (pending.size * 1.35))
                    if count > 0:
                        prefix = pending.lines[0][:count]
                        current.append(
                            Placed(
                                Block(
                                    lines=(prefix,),
                                    size=pending.size,
                                    height=count * pending.size * 1.35 + 8,
                                    bullet=pending.bullet,
                                    width=pending.width,
                                ),
                                top,
                            )
                        )
                        rest = pending.lines[0][count:]
                        pending = Block(
                            lines=(rest,),
                            size=pending.size,
                            height=len(rest) * pending.size * 1.35 + 8,
                            width=pending.width,
                        )
                elif pending.height > g.height - 2 * g.margin - repeat:
                    raise ValueError("table_row_or_image_exceeds_page")
                result.append(tuple(current))
                if g.flow:
                    current, top = [], g.margin
                else:
                    current = [Placed(heading, g.margin)]
                    top = g.margin + heading.height
    result.append(tuple(current))
    return tuple(result)
