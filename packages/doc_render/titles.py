"""A document's title is words, never a file name.

Staging -145 (2026-11-21): a deck built from a project note showed
"presentation-hirondelle" on its cover and in every footer. The outline gave
no title and the file stem stood in for it (project_document_input.normalize).
The class of defect is any deliverable (deck, document, workbook, PDF) titled
by a slug: words joined by hyphens or underscores, all lowercase, or the file
stem itself. Such a title is replaced by one written in words: the deck's
cover heading, else the stem read as words.
"""

from __future__ import annotations

import re
from typing import Any

from .cells import shown

EXTENSIONS = (".pptx", ".docx", ".xlsx", ".pdf", ".md", ".csv", ".txt", ".html")
LETTERS = re.compile(r"[^\W\d_]")
COPY = re.compile(r"\s\(\d+\)(?=(?:\.[A-Za-z]{2,4})?$)")


def slug_like(title: str, stem: str = "") -> bool:
    """Whether a title reads as a file name rather than words.

    A slug has no space and either names a file ("plan.pptx"), joins words
    with underscores, joins at least two words with hyphens in lowercase
    ("presentation-hirondelle", "rapport-2026"), or is the lowercase file stem.
    A word that takes a hyphen in natural writing is not one: "e-commerce",
    "Wi-Fi", "COVID-19".
    """
    # A numbered copy of a file name ("Four_Days_Full_Output (4)", the chat
    # saving a taken name, staging -170) is still the file name.
    text = COPY.sub("", " ".join(shown(title).split()))
    if not text or " " in text or not LETTERS.search(text):
        return False
    if text.casefold().endswith(EXTENSIONS):
        return True
    if "_" in text:
        return True
    if text != text.lower():
        return False
    if stem and text == stem.lower():
        return True
    parts = text.split("-")
    return (
        len(parts) >= 2
        and all(parts)
        and sum(len(part) >= 2 for part in parts) >= 2
        and any(len(LETTERS.findall(part)) >= 3 for part in parts)
    )


def in_words(title: str) -> str:
    """A slug read as words, in sentence case: "rapport_annuel" reads "Rapport annuel"."""
    text = shown(title).strip()
    for extension in EXTENSIONS:
        if text.casefold().endswith(extension):
            text = text[: -len(extension)]
    words = " ".join(re.sub(r"[-_.]+", " ", text).split())
    return words[:1].upper() + words[1:] if words else title


def natural(title: str, stem: str = "", headings: tuple[str, ...] = ()) -> str:
    """The title itself, or for a slug the first heading written in words.

    The headings are the candidates in order (a deck's cover heading); the
    stem read as words is the last resort.
    """
    if not slug_like(title, stem):
        return title
    for heading in headings:
        if heading.strip() and not slug_like(heading, stem):
            return heading
    return in_words(title)


# A line that names a document: a few words, not a sentence.
TITLE_WORDS = 15
TITLE_CHARACTERS = 120


def title_line(text: str) -> bool:
    """Whether a paragraph reads as a title line: short, one line, and not a
    sentence (no full stop, question or exclamation mark at its end)."""
    plain = " ".join(shown(text).split())
    return (
        bool(LETTERS.search(plain))
        and "\n" not in text
        and len(plain) <= TITLE_CHARACTERS
        and len(plain.split()) <= TITLE_WORDS
        and not plain.endswith((".", "!", "?", ":", ";", "\u2026"))
    )


def retitled(content: dict[str, Any], stem: str, *, deck: bool) -> dict[str, Any]:
    """A content object (as dumped) whose slug title is replaced by words.

    A deck takes its cover heading. A document whose text opens with a
    title line (title_line, before any heading) takes it, moved out
    of the body (the first "# " line of a Markdown text is its title already,
    project_document_input.normalize). Staging -156 (2026-11-22): a report rewritten after a
    refusal came without a title argument and opened with the line "Four
    Days, Full Output"; the file was titled "Four Days Full Output" from its
    name, with the asked title below it as body text, and both later
    versions kept the wrong title. The stem read as words is the last resort.
    A heading that repeated the slug (a Markdown text with no "# " line
    opens a section under the title) takes the new title too.
    """
    title = content.get("title")
    sections = list(content.get("sections") or [])
    if not isinstance(title, str) or not slug_like(title, stem):
        return content
    first = sections[0] if sections and isinstance(sections[0], dict) else None
    headings: tuple[str, ...] = ()
    if first is not None:
        heading = str(first.get("heading", ""))
        paragraphs = list(first.get("paragraphs") or [])
        if deck:
            headings = (heading,)
        elif heading == title and paragraphs and title_line(str(paragraphs[0])):
            headings = (str(paragraphs[0]),)
            first = dict(first, paragraphs=paragraphs[1:])
            body = ("paragraphs", "bullets", "tables", "images", "metrics", "notes")
            if any(first.get(part) for part in body) or first.get("chart"):
                sections[0] = first
            else:
                sections = sections[1:]
            if not sections:
                return content
    new = natural(title, stem, headings)
    return dict(
        content,
        title=new,
        sections=[
            dict(section, heading=new)
            if isinstance(section, dict) and section.get("heading") == title
            else section
            for section in sections
        ],
    )
