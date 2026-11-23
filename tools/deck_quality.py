"""deck_quality.py DECK.pptx...: render each deck with LibreOffice and score it.

The class of defect: a deck of white slides, a title over centred default
bullets, no colour and no shape (staging 2026-11-20). For each deck this prints
one JSON line: slide count, distinct layouts (shape geometry), slides with a
filled shape, chart, table or picture, plain white slides, mean empty share
of the rendered slides (share of the dominant colour), speaker notes, and the
reasons it fails. A deck fails with fewer than 3 slides (unless asked for
fewer), a single layout for every slide, every slide plain white with text
only, or a mean empty share over 0.9. Rendering needs `soffice` and
`pdftoppm`; without them only the file is scored.

Three more defects of staging -145 (2026-11-21) fail a deck: a title that is
a file name ("presentation-hirondelle", packages/doc_render/titles.py), a
word cut in two at a line end of the rendered PDF ("communicatio" / "n",
packages/doc_render/wordbreaks.py), and Sources slides that are not one
section before the closing slide (two Sources sections, or the last slide
drawn dark and titled Sources).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
from pptx import Presentation
from pptx.enum.dml import MSO_FILL
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.shapes.picture import Picture

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rendered(path: Path, directory: Path) -> list[Path]:
    if not (shutil.which("soffice") and shutil.which("pdftoppm")):
        return []
    pdf = converted(path, directory)
    subprocess.run(
        ["pdftoppm", "-r", "30", "-png", str(pdf), str(directory / "page")],
        check=True,
        timeout=120,
    )
    return sorted(
        directory.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1])
    )


def converted(path: Path, directory: Path) -> Path:
    """The deck as LibreOffice prints it."""
    profile = directory / "profile"
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(directory),
            str(path),
        ],
        capture_output=True,
        timeout=180,
        check=True,
    )
    return directory / (path.stem + ".pdf")


def deck_text(deck: object) -> str:
    """Every text of the deck's slides, the source of the cut-word check."""
    parts: list[str] = []
    for slide in deck.slides:  # type: ignore[attr-defined]
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                parts.extend(c.text for row in shape.table.rows for c in row.cells)
    return "\n".join(parts)


def cut_words(pdf: Path, source: str) -> list[str]:
    from pypdf import PdfReader

    from packages.doc_render.wordbreaks import page_lines, split_words

    return [
        word
        for page in PdfReader(pdf).pages
        for word in split_words(page_lines(page.extract_text() or ""), source)
    ]


def slide_title(slide: object) -> str:
    for shape in slide.shapes:  # type: ignore[attr-defined]
        if shape.name == "atlas:title" and shape.has_text_frame:
            return " ".join(shape.text_frame.text.split())
    return ""


def sources_defects(deck: object) -> list[str]:
    """Sources slides that are not one run, or a dark closing Sources slide."""
    titles = [slide_title(slide) for slide in deck.slides]  # type: ignore[attr-defined]
    runs = sum(
        1
        for index, title in enumerate(titles)
        if title == "Sources" and (index == 0 or titles[index - 1] != "Sources")
    )
    reasons = []
    if runs > 1:
        reasons.append(f"{runs} Sources sections")
    slides = list(deck.slides)  # type: ignore[attr-defined]
    if (
        len(slides) > 1
        and titles[-1] == "Sources"
        and slides[-1].background.fill.type == MSO_FILL.SOLID
    ):
        reasons.append("closing slide titled Sources")
    return reasons


def empty_share(png: Path) -> tuple[float, bool]:
    """The share of the dominant colour, and whether it is near white."""
    with Image.open(png) as image:
        small = image.convert("RGB")
        small.thumbnail((240, 240))
        counts: dict[tuple[int, int, int], int] = {}
        flat = getattr(small, "get_flattened_data", small.getdata)
        pixels = list(flat())
    for r, g, b in pixels:
        key = (r // 16, g // 16, b // 16)
        counts[key] = counts.get(key, 0) + 1
    colour, count = max(counts.items(), key=lambda item: item[1])
    return count / len(pixels), all(channel >= 15 for channel in colour)


def score(path: Path, asked: int | None = None) -> dict[str, object]:
    deck = Presentation(str(path))
    layouts: set[tuple[int, ...]] = set()
    area = (deck.slide_width or 1) * (deck.slide_height or 1)
    visual = notes = 0
    for slide in deck.slides:
        layouts.add(
            tuple(sorted((shape.left or 0) // 200000 for shape in slide.shapes))
        )
        if any(
            shape.shape_type
            in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.CHART, MSO_SHAPE_TYPE.TABLE)
            # A picture placed in a layout's picture placeholder.
            or isinstance(shape, Picture)
            or getattr(shape, "has_chart", False)
            or getattr(shape, "has_table", False)
            or (
                # A drawn shape that counts: at least 0.5 % of the slide (a
                # bullet dot drawn as a shape does not). Freeforms and groups
                # of shapes are drawings too (audit 2026-11-21).
                shape.shape_type
                in (
                    MSO_SHAPE_TYPE.AUTO_SHAPE,
                    MSO_SHAPE_TYPE.FREEFORM,
                    MSO_SHAPE_TYPE.GROUP,
                )
                and (shape.width or 0) * (shape.height or 0) >= area / 200
            )
            for shape in slide.shapes
        ):
            visual += 1
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            notes += 1
    count = len(deck.slides)
    broken: list[str] = []
    with tempfile.TemporaryDirectory(prefix="deck-quality-") as directory:
        shares = [empty_share(png) for png in rendered(path, Path(directory))]
        pdf = Path(directory) / (path.stem + ".pdf")
        if pdf.exists():
            broken = cut_words(pdf, deck_text(deck))
    from packages.doc_render.titles import slug_like

    # The title in the file's properties and the largest title of the cover.
    titles = [deck.core_properties.title or ""] + [
        " ".join(shape.text_frame.text.split())
        for shape in (deck.slides[0].shapes if count else ())
        if shape.name in ("atlas:deck-title", "atlas:title") and shape.has_text_frame
    ][:1]
    slugs = [title for title in titles if title and slug_like(title, path.stem)]
    result: dict[str, object] = dict(
        deck=path.name,
        slides=count,
        distinct_layouts=len(layouts),
        slides_with_visuals=visual,
        slides_with_notes=notes,
        rendered=len(shares),
        plain_white_slides=sum(white for _, white in shares),
        mean_empty=round(sum(share for share, _ in shares) / len(shares), 3)
        if shares
        else None,
        cut_words=broken,
    )
    reasons = []
    if count < min(3, asked or 3):
        reasons.append(f"{count} slides")
    if count > 1 and len(layouts) < 2:
        reasons.append("one layout for every slide")
    if visual == 0:
        reasons.append("text only: no shape, chart, table or picture")
    if shares and sum(share for share, _ in shares) / len(shares) > 0.9:
        reasons.append("slides mostly empty")
    if slugs:
        reasons.append(f"title is a file name: {slugs[0]}")
    if broken:
        reasons.append("words cut at a line end: " + ", ".join(broken[:5]))
    reasons.extend(sources_defects(deck))
    result["fail_reasons"] = reasons
    return result


if __name__ == "__main__":
    for name in sys.argv[1:]:
        print(json.dumps(score(Path(name)), ensure_ascii=False), flush=True)
