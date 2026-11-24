"""One structured view of what Atlas produced, whatever its format.

Every checker reads the same shape: ordered blocks, each with a kind
(heading, paragraph, bullet, numbered, checkbox, code, quote, file, picture,
cell, control), its visible text, the page or slide it is on and its region
(title, body, notes, footer, header). The reply in the conversation and a
delivered PPTX, DOCX, PDF, XLSX, HTML or SVG file become this view, so one
checker of "begins with", "in this order" or "three bullets per slide" works
on all of them.

Readers are bounded (pages, characters) and run offline: python-pptx,
python-docx, openpyxl, the standard HTML parser and pdftotext (poppler), as
the document read-back already does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ElementTree

MAX_BLOCKS = 4000
MAX_PAGES = 60


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    page: int = 0
    region: str = "body"


@dataclass
class View:
    """kind: answer, deck, document, pdf, workbook, page, drawing."""

    kind: str
    blocks: list[Block] = field(default_factory=list)
    pages: int = 0
    # False when the format draws boxes to tick as shapes (a PDF): a list
    # item may then be a checkbox the text does not show.
    boxes_known: bool = True

    def add(self, kind: str, text: str, page: int = 0, region: str = "body") -> None:
        text = " ".join(text.split())
        if (text or kind in {"file", "picture"}) and len(self.blocks) < MAX_BLOCKS:
            self.blocks.append(Block(kind, text, page, region))

    def select(self, page: int = 0, region: str = "") -> list[Block]:
        return [
            block
            for block in self.blocks
            if (not page or block.page == page)
            and (not region or block.region == region)
        ]

    def text(self, page: int = 0, region: str = "") -> str:
        return "\n".join(block.text for block in self.select(page, region))


# ---- the reply -----------------------------------------------------------------

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s++(.*)$")
_BOLD_LINE = re.compile(r"^\s*+\*\*([^*\n]{1,120})\*\*\s*+:?\s*+$")
_CHECKBOX = re.compile(r"^\s*+(?:[-*+]\s++)?(?:\[[ xX]\]|[☐☑☒□■✅⬜])\s++(.*)$")
_BULLET = re.compile(r"^\s*+(?:[-*+•▪◦‣]|·)\s++(.*)$")
_NUMBERED = re.compile(r"^\s*+\d{1,3}[.)]\s++(.*)$")
_QUOTE = re.compile(r"^\s*+>\s?(.*)$")
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]*)\)")
# A link to what Atlas produced: an app version, a saved file, a picture.
_OWN = re.compile(r"^(?:https?://[^/\s]+)?/(?:api/atlas/|images/)")
_PLAN_STEP = re.compile(r"^\s*[-*]\s+[✓⊘○✗✔]")
_DETAILS = re.compile(r"<details>.*?</details>", re.S)


def _visible(line: str) -> str:
    """A markdown line as read: link labels kept, markup gone."""
    line = _LINK.sub(lambda m: m.group(2), line)
    line = re.sub(r"<[^>\n]{1,80}>", " ", line)
    line = re.sub(r"(\*\*|__|\*|_|`)", "", line)
    return line.strip()


def answer_view(answer: str) -> View:
    """The reply as the person reads it, without the checked plan card and the
    collapsed step details."""
    view = View("answer")
    text = _DETAILS.sub("", answer)
    lines = text.splitlines()
    fence = ""
    code: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            view.add("paragraph", " ".join(paragraph))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        marker = _FENCE.match(line)
        if fence:
            if marker and marker.group(1)[0] == fence[0]:
                view.add("code", "\n".join(code))
                code, fence = [], ""
            else:
                code.append(line)
            continue
        if marker:
            flush()
            fence = marker.group(1)
            continue
        if not line.strip():
            flush()
            continue
        # The plan card: a bold title line followed by ticked steps.
        if _PLAN_STEP.match(line) or (
            line.lstrip().startswith("**")
            and index < len(lines)
            and _PLAN_STEP.match(next((n for n in lines[index:] if n.strip()), ""))
        ):
            flush()
            continue
        links = list(_LINK.finditer(line))
        own = [m for m in links if _OWN.match(m.group(3))]
        rest = _LINK.sub("", line)
        rest = re.sub(r"[·|\-–—:*_`\s]+", " ", rest).strip()
        if own and len(re.findall(r"\w+", rest)) <= 6:
            # A line of links to what Atlas produced: the deliverable.
            flush()
            for match in own:
                view.add("picture" if match.group(1) else "file", match.group(2))
            continue
        for pattern, kind in (
            (_HEADING, "heading"),
            (_CHECKBOX, "checkbox"),
            (_BULLET, "bullet"),
            (_NUMBERED, "numbered"),
            (_QUOTE, "quote"),
        ):
            found = pattern.match(line)
            if found:
                flush()
                view.add(kind, _visible(found.group(1)))
                break
        else:
            bold = _BOLD_LINE.match(line)
            if bold and not paragraph:
                view.add("heading", bold.group(1))
            else:
                paragraph.append(_visible(line))
    if fence and code:
        view.add("code", "\n".join(code))
    flush()
    return view


# ---- files ---------------------------------------------------------------------


def file_view(path: str | Path) -> View | None:
    """The view of a delivered file, or None for a format not read here."""
    path = Path(path)
    suffix = path.suffix.casefold()
    try:
        if suffix == ".pptx":
            return _deck(path)
        if suffix == ".docx":
            return _document(path)
        if suffix == ".pdf":
            return _pdf(path)
        if suffix in {".xlsx", ".xlsm"}:
            return _workbook(path)
        if suffix in {".html", ".htm"}:
            return html_view(path.read_text(encoding="utf-8", errors="replace"))
        if suffix == ".svg":
            return svg_view(path.read_text(encoding="utf-8", errors="replace"))
        if suffix in {".md", ".txt"}:
            view = answer_view(path.read_text(encoding="utf-8", errors="replace"))
            view.kind = "document"
            return view
    except Exception:  # noqa: BLE001 - an unreadable file is not checked, never failed
        return None
    return None


# A themed deck (packages/doc_render/deck.py) names each text shape by its
# role; readback.py reads it the same way.
ROLE = "atlas:"
TITLE_ROLES = frozenset({"title", "deck-title"})
FOOTER_ROLES = frozenset({"footer", "number"})
SKIPPED_ROLES = frozenset({"decor", "chart-title"})
# A card, a column or a step of a themed slide: a text of its own, not a
# point of a bulleted list (two to four developed points are drawn so).
CARD_ROLES = frozenset({"point", "metric-value", "metric-label"})
BOXES = ("\u2610", "\u2611", "\u2612", "\u25a1")
# renderer.build_slides: a slide's heading is drawn at 19 pt or more.
HEADING_POINTS = 17.0
_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _bullet_of(paragraph: object) -> tuple[bool, bool]:
    """(bulleted, numbered) from the paragraph's own properties; None when
    the paragraph says nothing (it inherits)."""
    properties = paragraph._p.find(f"{_NS}pPr")  # type: ignore[attr-defined]
    if properties is None:
        return False, False
    if properties.find(f"{_NS}buAutoNum") is not None:
        return True, True
    if properties.find(f"{_NS}buChar") is not None:
        return True, False
    return False, False


def _explicit_none(paragraph: object) -> bool:
    properties = paragraph._p.find(f"{_NS}pPr")  # type: ignore[attr-defined]
    return properties is not None and properties.find(f"{_NS}buNone") is not None


def _list_line(
    view: View, text: str, number: int, bullet: bool, numbered: bool
) -> None:
    stripped = text.strip()
    if stripped.startswith(BOXES):
        view.add("checkbox", stripped[1:], number)
        return
    if re.match(r"^[•▪◦‣\-–]\s", stripped):
        bullet, stripped = True, stripped[2:]
    view.add(
        "numbered" if numbered else "bullet" if bullet else "paragraph",
        stripped,
        number,
    )


def _largest(shape: object) -> float:
    sizes = [
        run.font.size.pt
        for paragraph in shape.text_frame.paragraphs  # type: ignore[attr-defined]
        for run in paragraph.runs
        if run.font.size is not None
    ]
    return max(sizes, default=0.0)


def _deck(path: Path) -> View:
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER

    view = View("deck")
    deck = Presentation(str(path))
    body_types = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT}
    for number, slide in enumerate(deck.slides, start=1):
        if number > MAX_PAGES:
            break
        view.pages = number
        shapes = sorted(
            slide.shapes, key=lambda s: ((s.top or 0) // 20000, s.left or 0)
        )
        texts = [s for s in shapes if s.has_text_frame and s.text_frame.text.strip()]
        themed = any(shape.name.startswith(ROLE) for shape in shapes)
        titled = False
        # Without a title placeholder or a role, the slide's title is its
        # topmost text in the largest size, when that is a heading size.
        fallback = None
        if not themed and not any(
            s.is_placeholder
            and s.placeholder_format.type
            in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
            for s in texts
        ):
            largest = max((_largest(s) for s in texts), default=0.0)
            if largest >= HEADING_POINTS:
                fallback = next(s for s in texts if _largest(s) == largest)
        for shape in texts:
            frame = shape.text_frame
            role = shape.name[len(ROLE) :] if shape.name.startswith(ROLE) else ""
            placeholder = (
                shape.placeholder_format.type if shape.is_placeholder else None
            )
            if (
                role in TITLE_ROLES
                or placeholder in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
                or shape is fallback
            ) and not titled:
                titled = True
                view.add("heading", frame.text, number, "title")
                continue
            if role in TITLE_ROLES:
                view.add("heading", frame.text, number)
                continue
            if role in FOOTER_ROLES or placeholder in {
                PP_PLACEHOLDER.FOOTER,
                PP_PLACEHOLDER.SLIDE_NUMBER,
            }:
                view.add("paragraph", frame.text, number, "footer")
                continue
            if role in SKIPPED_ROLES:
                continue
            if role in CARD_ROLES:
                view.add("card", frame.text, number)
                continue
            inherits = placeholder in body_types
            for paragraph in frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs) or paragraph.text
                if not text.strip():
                    continue
                bullet, numbered = _bullet_of(paragraph)
                if inherits and not _explicit_none(paragraph):
                    bullet = True
                _list_line(view, text, number, bullet, numbered)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                view.add("paragraph", notes.text, number, "notes")
    return view


def _document(path: Path) -> View:
    import docx

    view = View("document")
    document = docx.Document(str(path))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for section in document.sections:
        for region, part in (("header", section.header), ("footer", section.footer)):
            try:
                if part.is_linked_to_previous and section is not document.sections[0]:
                    continue
                for paragraph in part.paragraphs:
                    view.add("paragraph", paragraph.text, 0, region)
            except (AttributeError, ValueError):
                continue
    page = 1
    titled = False
    for paragraph in document.paragraphs:
        xml = paragraph._p.xml
        text = paragraph.text.strip()
        if 'w:type="page"' in xml or "w:pageBreakBefore" in xml:
            page += 1
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        folded = style.casefold()
        numbered = paragraph._p.find(f"{namespace}pPr/{namespace}numPr") is not None
        if folded == "title" and not titled:
            titled = True
            view.add("heading", text, page, "title")
            continue
        if folded.startswith(("heading", "title", "titre", "überschrift")):
            kind = "heading"
        elif text.startswith(BOXES):
            kind, text = "checkbox", text[1:]
        elif folded.startswith("list number"):
            kind = "numbered"
        elif folded.startswith("list") or numbered or re.match(r"^[•▪◦‣]\s", text):
            kind = "bullet"
            text = re.sub(r"^[•▪◦‣]\s", "", text)
        else:
            kind = "paragraph"
        view.add(kind, text, page)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                view.add("cell", cell.text, page)
    view.pages = page
    return view


# readback.py: the running footer of a PDF this repository draws sits under
# this height; a line in the bottom margin of another PDF is read as one too.
FOOTER_POINTS = 48.0
# Another PDF: a last line set apart near the bottom of its page.
FOOT_ZONE_POINTS = 100.0
TITLE_POINTS = 21.0
SUBHEADING_POINTS = 14.5
INDENT_POINTS = 8.0
# "\x7f": a bullet of a standard PDF font as some readers decode it.
_PDF_BULLET = re.compile(r"^\s*+(?:[•▪◦‣●○■\x7f·]\s*+|[\-–]\s++)(.*)$")
_PDF_NUMBER = re.compile(r"^\s*+\d{1,2}[.)]\s++(.*)$")


def _pdf(path: Path) -> View:
    """Lines with their size and place (readback._pdf_lines), joined into
    blocks: a line just under another at the same indent and size goes on
    the same paragraph or list item. A box to tick is drawn, not written:
    checkboxes and bullets are not told apart here (boxes_known)."""
    from packages.doc_render.readback import _pdf_lines

    view = View("pdf", boxes_known=False)
    _, lines = _pdf_lines(path.read_bytes()[: 64 * 1024 * 1024])
    if not lines:
        return view
    footers: set[int] = set()
    for index, line in enumerate(lines):
        before = lines[index - 1] if index else None
        after = lines[index + 1] if index + 1 < len(lines) else None
        final = after is None or after.page != line.page
        apart = (
            before is None
            or before.page != line.page
            or (before.y - line.y > 3 * line.size)
        )
        if line.y < FOOTER_POINTS or (final and apart and line.y < FOOT_ZONE_POINTS):
            footers.add(id(line))
    body = [line for line in lines if id(line) not in footers]
    margin = min((line.x for line in body), default=0.0)
    previous = None
    titled: set[int] = set()
    for line in lines:
        view.pages = max(view.pages, line.page)
        if line.page > MAX_PAGES:
            break
        if id(line) in footers:
            view.add("paragraph", line.text, line.page, "footer")
            previous = None
            continue
        same_block = (
            previous is not None
            and previous.page == line.page
            and previous.size == line.size
            and abs(previous.x - line.x) < 0.5
            and 0 < previous.y - line.y <= line.size * 1.35 * 1.25
            and view.blocks
        )
        if same_block and view.blocks[-1].kind != "heading":
            last = view.blocks.pop()
            view.add(last.kind, last.text + " " + line.text, last.page, last.region)
            previous = line
            continue
        previous = line
        if line.size >= SUBHEADING_POINTS:
            if line.page == 1 and line.size >= TITLE_POINTS and 1 not in titled:
                titled.add(1)
                view.add("heading", line.text, line.page, "title")
            else:
                view.add("heading", line.text, line.page)
            continue
        bullet = _PDF_BULLET.match(line.text)
        numbered = _PDF_NUMBER.match(line.text)
        if bullet:
            view.add("bullet", bullet.group(1), line.page)
        elif numbered:
            view.add("numbered", numbered.group(1), line.page)
        elif line.x - margin > INDENT_POINTS:
            view.add("bullet", line.text, line.page)
        else:
            view.add("paragraph", line.text, line.page)
    return view


def _workbook(path: Path) -> View:
    import openpyxl

    view = View("workbook")
    book = openpyxl.load_workbook(str(path), read_only=True, data_only=False)
    for number, sheet in enumerate(book.worksheets, start=1):
        if number > MAX_PAGES:
            break
        view.pages = number
        view.add("heading", sheet.title, number, "title")
        for row in sheet.iter_rows(max_row=500, max_col=40, values_only=True):
            for value in row:
                if value is not None:
                    view.add("cell", str(value), number)
    book.close()
    return view


class _Html(HTMLParser):
    _SKIP = {"script", "style", "template", "noscript", "head"}
    _BLOCKS = {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "td",
        "th",
        "span",
        "label",
        "figcaption",
        "caption",
        "main",
        "nav",
        "aside",
        "body",
    }

    def __init__(self, view: View) -> None:
        super().__init__(convert_charrefs=True)
        self.view = view
        self.stack: list[str] = []
        self.lists: list[str] = []
        self.buffer: list[str] = []
        self.kind = "paragraph"
        self.skip = 0

    def _flush(self) -> None:
        text = " ".join("".join(self.buffer).split())
        region = (
            "footer"
            if "footer" in self.stack
            else "header"
            if "header" in self.stack
            else "body"
        )
        if text:
            self.view.add(self.kind, text, 0, region)
        self.buffer = []
        self.kind = "paragraph"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skip += 1
            return
        if tag in {"ul", "ol"}:
            self._flush()
            self.lists.append(tag)
        elif tag == "li":
            self._flush()
            self.kind = "numbered" if self.lists[-1:] == ["ol"] else "bullet"
        elif re.fullmatch(r"h[1-6]|title", tag):
            self._flush()
            self.kind = "heading"
        elif tag in {"button", "a"}:
            self._flush()
            self.kind = "control"
        elif tag == "input":
            values = dict(attrs)
            if (values.get("type") or "").casefold() == "checkbox":
                self.view.add(
                    "checkbox", values.get("aria-label") or values.get("value") or ""
                )
            elif (values.get("type") or "").casefold() in {
                "button",
                "submit",
            } and values.get("value"):
                self.view.add("control", values.get("value") or "")
        elif tag in self._BLOCKS or tag == "br":
            self._flush()
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in {"ul", "ol"} and self.lists:
            self._flush()
            self.lists.pop()
        elif (
            tag in {"li", "button", "a", "title"}
            or re.fullmatch(r"h[1-6]", tag)
            or tag in self._BLOCKS
        ):
            self._flush()
        while self.stack and self.stack.pop() != tag:
            continue

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.buffer.append(data)


def html_view(source: str) -> View:
    view = View("page")
    parser = _Html(view)
    parser.feed(source[:400_000])
    parser.close()
    parser._flush()
    return view


def svg_view(source: str) -> View:
    """Text of a drawing, top to bottom; regions from the drawing's height."""
    view = View("drawing")
    try:
        root = ElementTree.fromstring(source[:400_000])
    except ElementTree.ParseError:
        return view
    box = (root.get("viewBox") or "").replace(",", " ").split()
    try:
        height = (
            float(box[3])
            if len(box) == 4
            else float(re.sub(r"[^\d.]", "", root.get("height") or "") or 0)
        )
    except ValueError:
        height = 0.0
    found: list[tuple[float, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "text":
            continue
        text = "".join(element.itertext())
        try:
            y = float(re.sub(r"[^\d.\-]", "", element.get("y") or "") or 0)
        except ValueError:
            y = 0.0
        found.append((y, text))
    for y, text in sorted(found, key=lambda pair: pair[0]):
        region = "body"
        if height:
            region = (
                "header"
                if y < height / 3
                else "footer"
                if y > 2 * height / 3
                else "body"
            )
        view.add("paragraph", text, 0, region)
    return view
