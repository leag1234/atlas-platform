"""Words and numbers cut in two at a line end ("communicatio" / "n").

Staging -145 (2026-11-21): four narrow cards of a deck broke a word inside
itself. The renderer keeps words whole (deck.fit); these checks find a word
that is still cut, in two ways:

- split_words reads rendered text (a PDF page, line by line): a line that
  ends inside a word and a next line that goes on in lowercase, where the
  source text holds the two pieces as one word and never as two words. No
  dictionary: the source text is the reference. A number cut at its
  thousands separator or from its unit ("3" / "000 personnes", "20" / "%")
  is a cut too when the source writes them together.
- overflowing measures a themed deck: a word wider than its text box at its
  size (Vera metrics, those the deck is fitted with) is cut where the box
  ends in every viewer.

A number group joined by a breakable space (spacing.py binds them) is
reported as well.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .cells import shown
from .spacing import UNITS, unbound, words

LETTERS = r"[^\W\d_]+"
_END = re.compile(f"({LETTERS})$")
_START = re.compile(f"^({LETTERS})")
_NUMBER_END = re.compile(r"(?<![\w.,])(\d{1,3})$")
_NUMBER_START = re.compile(rf"^(\d{{3}}(?!\d)|(?:{UNITS})(?!\w))")
BOLD = 1.08  # as deck.BOLD


def split_words(lines: Iterable[str], source: str) -> list[str]:
    """The words of the source that a line break cut in two, as "piece|piece".

    The source holds one text per line: a number is cut only when one text
    writes its two pieces together (a table's cells "Q3" and "120" are not
    one number).
    """
    compact = " ".join(source.split())
    texts = [" ".join(line.split()) for line in source.splitlines()]
    words = set(re.findall(LETTERS, compact))
    found: list[str] = []
    rows = [line.rstrip() for line in lines]
    for before, after in zip(rows, rows[1:]):
        number = _NUMBER_END.search(before)
        rest = _NUMBER_START.match(after.lstrip())
        group = (
            re.compile(rf"(?<![\w.,]){number[1]} {re.escape(rest[1])}(?![\d\w])")
            if number and rest
            else None
        )
        if group is not None and number and rest and any(map(group.search, texts)):
            found.append(f"{number[1]}|{rest[1]}")
            continue
        head = _END.search(before)
        tail = _START.match(after.lstrip())
        if head is None or tail is None or not tail[1][0].islower():
            continue
        joined = head[1] + tail[1]
        if f"{head[1]} {tail[1]}" in compact:
            continue
        if any(joined in word for word in words):
            found.append(f"{head[1]}|{tail[1]}")
    return found


def page_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def overflowing(deck: Any) -> list[str]:
    """The words of a themed deck wider than their box at their size."""
    from pptx.util import Emu

    from .layout import font, width_of

    font()
    found: list[str] = []

    def check(frame: Any, width: float) -> None:
        for paragraph in frame.paragraphs:
            properties = paragraph._p.pPr
            indent = (
                Emu(int(properties.get("marL", "0"))).pt
                if properties is not None
                else 0.0
            )
            for run in paragraph.runs:
                if run.font.size is None:
                    continue
                size = run.font.size.pt
                scale = BOLD if run.font.bold else 1.0
                found.extend(unbound(run.text))
                for word in words(shown(run.text)):
                    if width_of(word, size) * scale > width - indent + 0.5:
                        found.append(word)

    for slide in deck.slides:
        for shape in slide.shapes:
            name = getattr(shape, "name", "")
            if not name.startswith("atlas:") or name == "atlas:decor":
                continue
            if shape.has_text_frame:
                check(shape.text_frame, Emu(shape.width or 0).pt)
    return found
