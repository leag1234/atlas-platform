"""The parts a person asked to see on a slide are on the slide.

A deck's speaker notes are not the slide: a heading and a sentence the person
gave in quotes, written into the notes, are missing from what is shown; a
first title that became the subtitle under another deck title is not the
slide's title. Both are structure, not wording, and code can place them.

- ``requested`` reads the texts the person quoted (the same quotes as
  deliverable_check.quoted_texts) and whether each one is named as a title;
- ``placed`` repairs the content before it is built: a requested text held
  only by the speaker notes of a slide goes onto that slide (a title above
  the text, a sentence after the list), and a requested first title is the
  title of the first slide, never its subtitle;
- ``missing`` reads the built deck's slides (not their notes) and names the
  requested texts that are not on any of them.

A text the content does not hold at all is not invented here: the check of
the file against the request names it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

from packages.doc_render.cells import shown

# Words that name a title or heading, in the five interface languages.
TITLE_WORD = re.compile(
    r"(?<![\w-])(?:titles?|titled|headings?|headlines?|headers?|titres?|intitul\w*"
    r"|titel\w*|[uü]berschrift\w*|t[ií]tulos?|encabezados?|titol\w*|intestazion\w*"
    r"|rubrique|rubriek)(?![\w-])",
    re.I,
)
MIN_TEXT_WORDS = 3
MIN_LABEL_WORDS = 1
LABEL_SEPARATORS = (":", "–", "—", "-")


@dataclass(frozen=True)
class Part:
    text: str
    role: str  # "title" or "text"


def requested(requests: list[str]) -> list[Part]:
    """The quoted texts of the requests, in order, with their role."""
    from services.orchestrator.deliverable_check import QUOTES, _folded, _verbatim

    found: list[Part] = []
    for request in requests:
        for match in QUOTES.finditer(request):
            text = _verbatim(next(g for g in match.groups() if g is not None))
            words = len(_folded(text).split())
            before = request[max(0, match.start() - 60) : match.start()]
            # Only what precedes the quote in its own sentence names its role.
            before = re.split(r"[.!?\n]", before)[-1]
            role = "title" if TITLE_WORD.search(before) else "text"
            if words < (MIN_LABEL_WORDS if role == "text" else 1):
                continue
            if all(part.text != text for part in found):
                found.append(Part(text, role))
    return found


def _fold(text: str) -> str:
    from services.orchestrator.deliverable_check import _folded

    return _folded(shown(text))


def _slide_texts(section: Any) -> list[str]:
    from packages.doc_render.schema import slide_texts

    return [
        section.heading,
        *section.paragraphs,
        *section.bullets,
        *slide_texts(section),
    ]


def _on_slides(content: Any, folded: str) -> bool:
    texts = [content.title] + [t for s in content.sections for t in _slide_texts(s)]
    return any(folded and folded in _fold(text) for text in texts)


def placed(content: Any, parts: list[Part]) -> Any:
    """The content with the requested parts on their slides."""
    if not parts or not content.sections:
        return content
    data = content.model_dump(mode="json")
    sections: list[dict[str, Any]] = data["sections"]
    _labelled_bullets(sections, parts)
    for part in parts:
        folded = _fold(part.text)
        if not folded:
            continue
        if part.role == "title":
            _first_title(data, sections, part, folded)
        if _on_slides_data(data, sections, folded):
            continue
        for section in sections:
            notes = section["notes"]
            held = [i for i, note in enumerate(notes) if folded in _fold(note)]
            if not held:
                continue
            for i in reversed(held):
                if _fold(notes[i]) == folded:
                    del notes[i]
            if part.role == "title":
                section["paragraphs"] = [part.text, *section["paragraphs"]]
            elif section["bullets"]:
                # A sentence beside a list goes under it (a continued section,
                # which the deck draws on the same slide). A named part keeps
                # its own subheading instead of becoming another list item.
                at = sections.index(section) + 1
                sections.insert(
                    at,
                    dict(
                        _empty(section),
                        heading=section["heading"],
                        paragraphs=[part.text],
                        continued=True,
                        level=section["level"],
                    ),
                )
            else:
                section["paragraphs"] = [*section["paragraphs"], part.text]
            break
    from packages.doc_render.schema import Content

    return Content.model_validate(data)


def _labelled_bullets(sections: list[dict[str, Any]], parts: list[Part]) -> None:
    """A quoted label at the start of a bullet names a sub-part, not a point."""
    labels = [part.text for part in parts if part.role == "text"]
    if not labels:
        return
    for section in reversed(sections):
        bullets = section.get("bullets", [])
        for index in reversed(range(len(bullets))):
            label, separator, text = _split_label(bullets[index])
            if separator is None:
                continue
            named = next((text for text in labels if _fold(text) == _fold(label)), None)
            if named is None:
                continue
            sentence = text.strip()
            if not sentence:
                continue
            del bullets[index]
            sections.insert(
                sections.index(section) + 1,
                dict(
                    _empty(section),
                    heading=named,
                    paragraphs=[sentence],
                    continued=True,
                    level=2,
                    subheading=named,
                ),
            )


def _split_label(bullet: str) -> tuple[str, str | None, str]:
    for separator in LABEL_SEPARATORS:
        label, found, text = bullet.partition(separator)
        if found:
            return label, found, text
    return bullet, None, ""


def _empty(section: dict[str, Any]) -> dict[str, Any]:
    return dict(
        paragraphs=[],
        bullets=[],
        tables=[],
        images=[],
        notes=[],
        metrics=[],
        chart=None,
        sequence=False,
        page_break=False,
        level=section["level"],
    )


def _on_slides_data(
    data: dict[str, Any], sections: list[dict[str, Any]], folded: str
) -> bool:
    texts = [data["title"]]
    for section in sections:
        texts += [section["heading"], *section["paragraphs"], *section["bullets"]]
        for metric in section["metrics"]:
            texts += [metric["value"], metric["label"]]
        chart = section["chart"]
        if chart:
            texts += [chart.get("title") or "", *chart["labels"]]
    return any(folded in _fold(text) for text in texts if text)


def _first_title(
    data: dict[str, Any], sections: list[dict[str, Any]], part: Part, folded: str
) -> None:
    """A requested first title is the title of the first slide."""
    first = sections[0]
    if _fold(data["title"]) == folded and _fold(first["heading"]) in ("", folded):
        return
    held_heading = _fold(first["heading"]) == folded
    lines = [*first["paragraphs"], *first["bullets"]]
    held_line = next((i for i, t in enumerate(lines) if _fold(t) == folded), None)
    if not held_heading and held_line is None:
        return
    if held_line is not None and not held_heading:
        # The title sat under another heading: it becomes the heading, and
        # the old heading is no longer the title shown above it.
        key = "paragraphs" if held_line < len(first["paragraphs"]) else "bullets"
        index = (
            held_line if key == "paragraphs" else held_line - len(first["paragraphs"])
        )
        del first[key][index]
        first["heading"] = part.text
    if not data.get("footer") and _fold(data["title"]) != folded:
        # The deck's own title is not lost: it stays the running footer of
        # the slides, where the first slide's title would otherwise repeat.
        data["footer"] = data["title"]
    data["title"] = part.text


def missing(raw: bytes, parts: list[Part]) -> list[str]:
    """The requested texts that no slide of the built deck shows (the notes
    are not the slide)."""
    from pptx import Presentation

    try:
        deck = Presentation(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 - an unreadable file is not measured
        return []
    shown_text: list[str] = []
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                shown_text.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                shown_text.extend(c.text for row in shape.table.rows for c in row.cells)
    whole = " ".join(_fold(text) for text in shown_text)
    return [
        f"«{part.text}» is not on any slide (speaker notes are not the slide)"
        for part in parts
        if _fold(part.text)
        and _fold(part.text) not in whole
        and _fold(part.text) in _notes(deck)
    ]


def _notes(deck: Any) -> str:
    return " ".join(
        _fold(slide.notes_slide.notes_text_frame.text)
        for slide in deck.slides
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None
    )
