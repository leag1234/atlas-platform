"""The form of a paginated document, measured on its rendered PDF.

The class of defect: a six-section report of a few lines each delivered on
six pages, each about 95 % empty, because a page break preceded every heading
(staging -150, 2026-11-21). Its check counted pages and headings and passed.
deck_quality (tools/deck_quality.py) scores decks; this scores pages.

measure() reads a PDF (the renderer's own, or LibreOffice's print of a DOCX)
with pypdf: the position and size of every text line and the box of every
picture, page by page. findings() names three defects:

- a page that is not the last one and whose text and pictures span less than
  NEAR_EMPTY of its height (a cover page and a page that ends where a break
  was asked are exempt);
- more pages than the content needs: at least twice the filled shares of all
  pages added up over FULL_PAGE (a full page leaves its margins empty),
  rounded up, after one page for each page break decided by the layout
  (pictures and table rows are never cut, so a page may stay half empty);
- a heading as the last line of a page that is not the last one (an orphan
  heading: its text starts on the next page);
- a lead-in sentence (ending with a colon) as the last line of a page that
  is not the last one: the list or table it introduces starts on the next
  page (staging -151, "Recommendations" and "we recommend:" at the foot of
  page 1, the four points on page 2).

misgrouped() reads the content itself: several group titles written one
after another as paragraphs, then one list or table. A section writes its
paragraphs before its bullets, so the titles of three groups were printed
together over the nine points of the three lists (staging -183).

The measures are facts; the deliverable check (services/orchestrator/
deliverable_check.py) refuses a file once with them, verify() reports them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import io
import math
from pathlib import Path
from typing import Any

from .cells import shown
from .schema import label

# layout.FOOTER_ZONE: a line drawn under it is the running footer.
FOOTER_ZONE = 26.0

# A page spanned over less than this share of its height is nearly empty.
NEAR_EMPTY = 0.25
# The share of its height a full page spans: an A4 report page with 50 pt
# margins spans 0.88, a Letter page with one-inch margins 0.82.
FULL_PAGE = 0.8
# A line this much larger than the body text is a heading.
HEADING_RATIO = 1.15
# The pages read at most (a longer document is measured on its first pages).
MAX_PAGES = 300


@dataclass(frozen=True)
class Line:
    y: float
    size: float
    text: str


@dataclass(frozen=True)
class Page:
    """One page: its height and what it holds, in PDF points (y upwards)."""

    number: int
    height: float
    lines: tuple[Line, ...]
    # (bottom, top) of each picture drawn on the page.
    pictures: tuple[tuple[float, float], ...] = ()

    def edges(self) -> tuple[float, float] | None:
        """The bottom and the top of what the page holds, or None when blank."""
        bottoms = [line.y - 0.25 * line.size for line in self.lines]
        tops = [line.y + 0.8 * line.size for line in self.lines]
        bottoms.extend(bottom for bottom, _ in self.pictures)
        tops.extend(top for _, top in self.pictures)
        if not tops:
            return None
        return min(bottoms), max(tops)

    def filled(self) -> float:
        """The share of the page height between its first and last content."""
        edges = self.edges()
        if edges is None or self.height <= 0:
            return 0.0
        return max(0.0, min(1.0, (edges[1] - edges[0]) / self.height))

    def last_line(self) -> Line | None:
        """The lowest text line, when nothing is drawn below it."""
        if not self.lines:
            return None
        lowest = min(self.lines, key=lambda line: line.y)
        if any(bottom < lowest.y - lowest.size for bottom, _ in self.pictures):
            return None
        return lowest


@dataclass(frozen=True)
class Form:
    pages: tuple[Page, ...]
    body_size: float

    @property
    def count(self) -> int:
        return len(self.pages)

    def filled(self) -> tuple[float, ...]:
        return tuple(round(page.filled(), 3) for page in self.pages)

    def mean_empty(self) -> float:
        shares = self.filled()
        return round(1 - sum(shares) / len(shares), 3) if shares else 0.0

    def needed(self) -> int:
        """The pages the content needs when it flows."""
        return max(1, math.ceil(sum(page.filled() for page in self.pages) / FULL_PAGE))

    def orphan(self, page: Page) -> str | None:
        """The text of a heading that ends the page, or None."""
        line = page.last_line()
        if line is None or self.body_size <= 0:
            return None
        if line.size >= HEADING_RATIO * self.body_size:
            return line.text
        return None

    def lead_in(self, page: Page) -> str | None:
        """The text of a lead-in sentence that ends the page, or None."""
        line = page.last_line()
        if line is None or self.body_size <= 0:
            return None
        if line.size < HEADING_RATIO * self.body_size and line.text.rstrip().endswith(
            (":", "\uff1a")
        ):
            return line.text
        return None


def _matrix(a: Any, b: Any) -> tuple[float, ...]:
    """The product of two PDF matrices (a then b)."""
    a0, a1, a2, a3, a4, a5 = (float(v) for v in a)
    b0, b1, b2, b3, b4, b5 = (float(v) for v in b)
    return (
        a0 * b0 + a1 * b2,
        a0 * b1 + a1 * b3,
        a2 * b0 + a3 * b2,
        a2 * b1 + a3 * b3,
        a4 * b0 + a5 * b2 + b4,
        a4 * b1 + a5 * b3 + b5,
    )


def measure(source: bytes | Path) -> Form:
    """The lines and pictures of every page of a PDF."""
    from pypdf import PdfReader

    raw = source.read_bytes() if isinstance(source, Path) else source
    reader = PdfReader(io.BytesIO(raw), strict=False)
    result: list[Page] = []
    sizes: Counter[float] = Counter()
    for number, page in enumerate(reader.pages, 1):
        if number > MAX_PAGES:
            break
        lines: list[Line] = []
        pictures: list[tuple[float, float]] = []

        def text(value: str, cm: Any, tm: Any, font: Any, size: float) -> None:
            if not value.strip():
                return
            m = _matrix(tm, cm)
            if m[5] < FOOTER_ZONE:
                # The running footer (renderer.build_pdf) is no body text.
                return
            # The vertical scale of the text space gives the drawn size.
            drawn = round(abs(float(size)) * math.hypot(m[2], m[3]), 1)
            if drawn <= 0:
                return
            lines.append(Line(m[5], drawn, " ".join(value.split())))

        def operator(op: bytes, args: Any, cm: Any, tm: Any) -> None:
            if op == b"Do":
                # A picture fills the unit square of the current matrix.
                c = [float(v) for v in cm]
                ys = (c[5], c[5] + c[3], c[5] + c[1], c[5] + c[1] + c[3])
                if max(ys) - min(ys) > 2:
                    pictures.append((min(ys), max(ys)))

        page.extract_text(visitor_text=text, visitor_operand_before=operator)
        height = float(page.mediabox.height)
        # Text pieces of one line at one size: one line (a run per word in
        # some writers).
        merged: dict[tuple[int, float], Line] = {}
        for line in lines:
            key = (round(line.y), line.size)
            if key in merged:
                old = merged[key]
                merged[key] = Line(old.y, old.size, f"{old.text} {line.text}")
            else:
                merged[key] = line
        for line in merged.values():
            sizes[line.size] += 1
        result.append(Page(number, height, tuple(merged.values()), tuple(pictures)))
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    return Form(tuple(result), body)


def findings(
    form: Form, *, asked: set[int] | frozenset[int] = frozenset(), decided: int = 0
) -> list[str]:
    """The defects of form, as short English sentences (empty when none).

    asked holds the numbers (1-based) of the pages that end where a page
    break was asked (a title page included): such a page may be short.
    decided is the number of page breaks the layout placed by decision
    (layout.pages: asked ones and very long sections).
    """
    found: list[str] = []
    count = form.count
    for page in form.pages[:-1]:
        share = page.filled()
        if share < NEAR_EMPTY and page.number not in asked:
            found.append(
                f"page {page.number} of {count} is nearly empty "
                f"({round(share * 100)} % of its height filled) but is not the "
                "last page"
            )
    needed = form.needed()
    # Pictures and table rows are not cut: a picture book of one large
    # picture per page fills each page to less than half and needs them all.
    # Twice the pages the content needs is no longer such a layout.
    if count - decided >= max(needed + 1, 2 * needed):
        found.append(
            f"the file has {count} pages where its content needs about "
            f"{needed + decided}: sections must follow each other on the page"
        )
    for page in form.pages[:-1]:
        heading = form.orphan(page)
        if heading is not None and page.number not in asked:
            found.append(
                f'page {page.number} ends with the heading "{heading[:80]}" '
                "while its text starts on the next page"
            )
        introduction = form.lead_in(page)
        if introduction is not None and page.number not in asked:
            found.append(
                f'page {page.number} ends with the lead-in "{introduction[-80:]}" '
                "while what it introduces starts on the next page"
            )
    return found


# A paragraph this short, not ending a sentence, reads as a title.
TITLE_WORDS = 8


def titled(text: str) -> bool:
    """Whether a paragraph reads as the title of what follows it."""
    if label(text):
        return True
    plain = shown(text).strip()
    return 0 < len(plain.split()) <= TITLE_WORDS and not plain.endswith(
        (".", "!", "?", "\u3002")
    )


def misgrouped(content: Any) -> list[str]:
    """Sections whose group titles stand together over a single block.

    Two or more title-like paragraphs end a section's paragraphs, and its
    bullets or a table follow: each title has lost the text it named.
    """
    found: list[str] = []
    for section in getattr(content, "sections", ()):
        if not (section.bullets or section.tables):
            continue
        titles: list[str] = []
        for text in reversed(section.paragraphs):
            if not titled(text):
                break
            titles.insert(0, text)
        if len(titles) >= 2:
            named = ", ".join(f'"{shown(text).strip()[:60]}"' for text in titles[:4])
            found.append(
                f'the section "{shown(section.heading)[:80]}" writes {len(titles)} '
                f"group titles one after another ({named}) and then all its "
                f"{'points' if section.bullets else 'table rows'}: write each "
                'title as a "### " sub-heading followed by its own points'
            )
    return found


def summary(form: Form) -> dict[str, object]:
    """The measures of a form, for a report line."""
    return {
        "pages": form.count,
        "needed": form.needed(),
        "filled": list(form.filled()),
        "mean_empty": form.mean_empty(),
        "body_size": form.body_size,
    }


def layout_breaks(content: Any, root: Path) -> tuple[set[int], int]:
    """The pages that end where a break was asked, and the decided breaks.

    The layout (layout.pages) gives each section that starts a page by
    decision the index of that page; the page before it ends at the break.
    """
    from .layout import pages, report_geometry

    breaks: dict[int, int] = {}
    pages(content, root, geometry=report_geometry(content), breaks=breaks)
    asked = {
        start
        for index, start in breaks.items()
        if (content.sections[index].page_break if index else content.cover)
    }
    return asked, len(breaks)


def check(content: Any, root: Path, pdf: bytes | None = None) -> list[str]:
    """The form defects of a report built from content.

    pdf is the report the renderer built; without it (a DOCX), the PDF of
    the same content is built and measured: the DOCX places the same breaks
    (renderer.build_document) and flows like it.
    """
    import tempfile

    from .renderer import build_pdf

    content = content.flattened()
    asked, decided = layout_breaks(content, root)
    if pdf is None:
        with tempfile.TemporaryDirectory(prefix="doc-form-") as directory:
            path = Path(directory) / "form.pdf"
            build_pdf(content, path, root)
            pdf = path.read_bytes()
    return misgrouped(content) + findings(measure(pdf), asked=asked, decided=decided)
