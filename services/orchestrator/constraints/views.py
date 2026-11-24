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
import subprocess
import xml.etree.ElementTree as ElementTree

MAX_BLOCKS = 4000
MAX_PAGES = 60
PDF_SECONDS = 20


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


def _deck(path: Path) -> View:
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER

    view = View("deck")
    deck = Presentation(str(path))
    namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    body_types = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT}
    for number, slide in enumerate(deck.slides, start=1):
        if number > MAX_PAGES:
            break
        view.pages = number
        shapes = sorted(
            slide.shapes,
            key=lambda s: ((s.top or 0) // 20000, s.left or 0),
        )
        for shape in shapes:
            if not shape.has_text_frame:
                continue
            placeholder = None
            if shape.is_placeholder:
                placeholder = shape.placeholder_format.type
            if placeholder in {
                PP_PLACEHOLDER.TITLE,
                PP_PLACEHOLDER.CENTER_TITLE,
            }:
                view.add("heading", shape.text_frame.text, number, "title")
                continue
            if placeholder in {PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER}:
                view.add("paragraph", shape.text_frame.text, number, "footer")
                continue
            inherits_bullets = placeholder in body_types
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs) or paragraph.text
                if not text.strip():
                    continue
                properties = paragraph._p.find(f"{namespace}pPr")
                bullet = inherits_bullets
                numbered = False
                if properties is not None:
                    if properties.find(f"{namespace}buNone") is not None:
                        bullet = False
                    if properties.find(f"{namespace}buChar") is not None:
                        bullet = True
                    if properties.find(f"{namespace}buAutoNum") is not None:
                        bullet, numbered = True, True
                stripped = text.strip()
                if re.match(r"^[•▪◦‣\-–]\s", stripped):
                    bullet, stripped = True, stripped[2:]
                if re.match(r"^[☐☑☒□]\s?", stripped):
                    view.add("checkbox", stripped[1:], number)
                    continue
                kind = "numbered" if numbered else "bullet" if bullet else "paragraph"
                view.add(kind, stripped, number)
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
                for paragraph in part.paragraphs:
                    view.add("paragraph", paragraph.text, 0, region)
            except (AttributeError, ValueError):
                continue
    page = 1
    for paragraph in document.paragraphs:
        xml = paragraph._p.xml
        if 'w:type="page"' in xml or "w:pageBreakBefore" in xml:
            page += 1 if paragraph.text.strip() or 'w:type="page"' in xml else 0
        style = (paragraph.style.name if paragraph.style is not None else "").casefold()
        text = paragraph.text.strip()
        numbered = paragraph._p.find(f"{namespace}pPr/{namespace}numPr") is not None
        if style.startswith(("heading", "title", "titre", "überschrift")):
            kind = "heading"
        elif re.match(r"^[☐☑☒□]\s?", text):
            kind, text = "checkbox", text[1:]
        elif "list number" in style:
            kind = "numbered"
        elif "list" in style or numbered or re.match(r"^[•▪◦‣]\s", text):
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


_PDF_BULLET = re.compile(r"^\s*+([•▪◦‣●○■\-–])\s++(.*)$")
_PDF_BOX = re.compile(r"^\s*+([☐☑☒□❑❒])\s*+(.*)$")
_PDF_NUMBER = re.compile(r"^\s*+\d{1,2}[.)]\s++(.*)$")


def _pdf(path: Path) -> View:
    view = View("pdf")
    try:
        output = subprocess.run(
            [
                "pdftotext",
                "-layout",
                "-enc",
                "UTF-8",
                "-l",
                str(MAX_PAGES),
                str(path),
                "-",
            ],
            capture_output=True,
            timeout=PDF_SECONDS,
            check=True,
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return view
    pages = output.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    for number, page in enumerate(pages, start=1):
        view.pages = number
        lines = [line for line in page.splitlines() if line.strip()]
        if not lines:
            continue
        # Short last lines set apart at the foot of the page: the footer.
        footer_from = len(lines)
        raw = page.rstrip("\n").splitlines()
        trailing_blank = 0
        for line in reversed(raw[:-1] if raw and not raw[-1].strip() else raw):
            if line.strip():
                break
            trailing_blank += 1
        if len(lines) >= 3 and len(lines[-1].split()) <= 10:
            gap_before_last = raw[-2].strip() == "" if len(raw) >= 2 else False
            if gap_before_last or trailing_blank == 0:
                footer_from = len(lines) - 1
        for index, line in enumerate(lines):
            region = "footer" if index >= footer_from else "body"
            if index == 0:
                view.add("heading", line, number, "title")
                continue
            box = _PDF_BOX.match(line)
            bullet = _PDF_BULLET.match(line)
            numbered = _PDF_NUMBER.match(line)
            if box:
                view.add("checkbox", box.group(2), number, region)
            elif bullet:
                view.add("bullet", bullet.group(2), number, region)
            elif numbered:
                view.add("numbered", numbered.group(1), number, region)
            else:
                view.add("paragraph", line, number, region)
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
